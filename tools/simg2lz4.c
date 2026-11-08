/*
 * Native 64-bit Windows raw image -> Android Sparse Image -> Samsung LZ4 Frame
 * converter + full streaming LZ4F verifier for ios-sc54a Odin userdata.img.lz4.
 */
#define _CRT_SECURE_NO_WARNINGS
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <lz4frame.h>

#define SPARSE_HEADER_MAGIC  0xed26ff3aU
#define CHUNK_TYPE_RAW       0xCAC1U
#define CHUNK_TYPE_FILL      0xCAC2U
#define CHUNK_TYPE_DONT_CARE 0xCAC3U

#define BLK_SIZE        4096ULL
#define FILE_HDR_SZ     28ULL
#define CHUNK_HDR_SZ    12ULL
#define MAX_CHUNK_BLKS  16384U  /* 64 MiB max for ALL chunk types (prevents 32-bit Loke overflow!) */
#define FORCE_FILL_END  (64ULL * 1024ULL * 1024ULL) /* Always zero-fill first 64 MiB */
#define DONT_CARE_MIN   256U    /* >= 1 MiB zero runs past 64 MiB use DONT_CARE */

#pragma pack(push, 1)
typedef struct {
    uint32_t magic;
    uint16_t major_version;
    uint16_t minor_version;
    uint16_t file_hdr_sz;
    uint16_t chunk_hdr_sz;
    uint32_t blk_sz;
    uint32_t total_blks;
    uint32_t total_chunks;
    uint32_t image_checksum;
} sparse_header_t;

typedef struct {
    uint16_t chunk_type;
    uint16_t reserved1;
    uint32_t chunk_sz;
    uint32_t total_sz;
} chunk_header_t;
#pragma pack(pop)

typedef struct {
    uint16_t type;
    uint32_t count;
    uint64_t offset;
} chunk_desc_t;

static int is_zero_4k(const uint8_t *p) {
    const uint64_t *q = (const uint64_t *)p;
    for (size_t i = 0; i < BLK_SIZE / 8; ++i) {
        if (q[i] != 0) return 0;
    }
    return 1;
}

static int lz4_write_checked(FILE *out, LZ4F_compressionContext_t ctx,
                             uint8_t *dst_buf, size_t dst_cap,
                             const void *src, size_t src_len) {
    size_t n = LZ4F_compressUpdate(ctx, dst_buf, dst_cap, src, src_len, NULL);
    if (LZ4F_isError(n)) {
        fprintf(stderr, "ERROR: LZ4F_compressUpdate failed: %s\n", LZ4F_getErrorName(n));
        return -1;
    }
    if (n > 0 && fwrite(dst_buf, 1, n, out) != n) {
        fprintf(stderr, "ERROR: fwrite failed\n");
        return -1;
    }
    return 0;
}

static int verify_lz4_frame(const char *lz4_path, uint64_t expected_sparse_size) {
    FILE *fp = fopen(lz4_path, "rb");
    if (!fp) {
        fprintf(stderr, "ERROR: cannot open %s for verification\n", lz4_path);
        return -1;
    }
    uint8_t hdr[15];
    if (fread(hdr, 1, sizeof(hdr), fp) != sizeof(hdr)) {
        fprintf(stderr, "ERROR: truncated LZ4 header\n");
        fclose(fp);
        return -1;
    }
    uint32_t magic = *(const uint32_t *)(hdr + 0);
    uint8_t flg = hdr[4];
    uint8_t bd = hdr[5];
    uint64_t csize = *(const uint64_t *)(hdr + 6);
    printf("[verify] LZ4 header: magic=0x%08x flg=0x%02x bd=0x%02x contentSize=%llu (0x%llx)\n",
           magic, flg, bd, (unsigned long long)csize, (unsigned long long)csize);
    if (magic != 0x184d2204U || flg != 0x6cU || bd != 0x60U || csize != expected_sparse_size) {
        fprintf(stderr, "ERROR: header mismatch against Samsung LZ4 spec!\n");
        fclose(fp);
        return -1;
    }

    _fseeki64(fp, 0, SEEK_SET);
    LZ4F_dctx *dctx = NULL;
    size_t err = LZ4F_createDecompressionContext(&dctx, LZ4F_VERSION);
    if (LZ4F_isError(err)) {
        fprintf(stderr, "ERROR: LZ4F_createDecompressionContext: %s\n", LZ4F_getErrorName(err));
        fclose(fp);
        return -1;
    }

    const size_t IN_SZ = 4 * 1024 * 1024;
    const size_t OUT_SZ = 4 * 1024 * 1024;
    uint8_t *in_buf = (uint8_t *)malloc(IN_SZ);
    uint8_t *out_buf = (uint8_t *)malloc(OUT_SZ);
    uint64_t total_out = 0;
    int checked_sparse_hdr = 0;
    size_t ret = 1;

    while (!feof(fp)) {
        size_t rd = fread(in_buf, 1, IN_SZ, fp);
        if (rd == 0) break;
        size_t src_pos = 0;
        while (src_pos < rd) {
            size_t src_rem = rd - src_pos;
            size_t dst_cap = OUT_SZ;
            ret = LZ4F_decompress(dctx, out_buf, &dst_cap, in_buf + src_pos, &src_rem, NULL);
            if (LZ4F_isError(ret)) {
                fprintf(stderr, "ERROR: LZ4F_decompress failed: %s\n", LZ4F_getErrorName(ret));
                free(in_buf); free(out_buf); LZ4F_freeDecompressionContext(dctx); fclose(fp);
                return -1;
            }
            if (!checked_sparse_hdr && dst_cap >= sizeof(sparse_header_t)) {
                const sparse_header_t *sh = (const sparse_header_t *)out_buf;
                if (sh->magic != SPARSE_HEADER_MAGIC || sh->blk_sz != 4096) {
                    fprintf(stderr, "ERROR: bad sparse magic inside LZ4 stream!\n");
                    free(in_buf); free(out_buf); LZ4F_freeDecompressionContext(dctx); fclose(fp);
                    return -1;
                }
                checked_sparse_hdr = 1;
            }
            total_out += dst_cap;
            src_pos += src_rem;
        }
    }

    free(in_buf);
    free(out_buf);
    LZ4F_freeDecompressionContext(dctx);
    fclose(fp);

    if (ret != 0 || total_out != expected_sparse_size) {
        fprintf(stderr, "ERROR: LZ4 stream incomplete: ret=%zu total_out=%llu expected=%llu\n",
                ret, (unsigned long long)total_out, (unsigned long long)expected_sparse_size);
        return -1;
    }
    printf("[verify] PASSED 100%% streaming LZ4F decompression + xxHash32 checksum (%llu bytes)!\n",
           (unsigned long long)total_out);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <raw_hfs.img> <out_userdata.img.lz4>\n", argv[0]);
        return 2;
    }
    const char *raw_path = argv[1];
    const char *lz4_path = argv[2];

    FILE *rf = fopen(raw_path, "rb");
    if (!rf) {
        fprintf(stderr, "ERROR: cannot open %s\n", raw_path);
        return 1;
    }
    _fseeki64(rf, 0, SEEK_END);
    int64_t fsize_i = _ftelli64(rf);
    _fseeki64(rf, 0, SEEK_SET);
    if (fsize_i <= 0 || (fsize_i % BLK_SIZE) != 0) {
        fprintf(stderr, "ERROR: invalid raw image size %lld\n", (long long)fsize_i);
        return 1;
    }
    uint64_t fsize = (uint64_t)fsize_i;
    uint32_t total_blks = (uint32_t)(fsize / BLK_SIZE);

    size_t cap = 65536;
    size_t nchunks = 0;
    chunk_desc_t *chunks = (chunk_desc_t *)malloc(cap * sizeof(chunk_desc_t));

    const size_t WIN_SZ = MAX_CHUNK_BLKS * BLK_SIZE; /* 64 MiB */
    uint8_t *win = (uint8_t *)malloc(WIN_SZ);

    uint16_t cur_is_zero = 0xFFFFU;
    uint32_t cur_cnt = 0;
    uint64_t cur_off = 0;
    uint64_t pos = 0;

    printf("[scan] Scanning %s (%.2f GiB)...\n", raw_path, (double)fsize / (1024.0 * 1024.0 * 1024.0));
    fflush(stdout);

#define EMIT_RUN(is_z, cnt, off) do { \
        uint16_t rtype = CHUNK_TYPE_RAW; \
        if (is_z) { \
            rtype = ((off) >= FORCE_FILL_END && (cnt) >= DONT_CARE_MIN) \
                    ? CHUNK_TYPE_DONT_CARE : CHUNK_TYPE_FILL; \
        } \
        uint32_t rem_blks = (cnt); \
        uint64_t sub_off = (off); \
        while (rem_blks > 0) { \
            uint32_t step_blks = (rem_blks > MAX_CHUNK_BLKS) ? MAX_CHUNK_BLKS : rem_blks; \
            if (nchunks == cap) { \
                cap *= 2; \
                chunks = (chunk_desc_t *)realloc(chunks, cap * sizeof(chunk_desc_t)); \
            } \
            chunks[nchunks++] = (chunk_desc_t){rtype, step_blks, sub_off}; \
            sub_off += (uint64_t)step_blks * BLK_SIZE; \
            rem_blks -= step_blks; \
        } \
    } while (0)

    while (pos < fsize) {
        size_t to_read = (fsize - pos > WIN_SZ) ? WIN_SZ : (size_t)(fsize - pos);
        if (fread(win, 1, to_read, rf) != to_read) {
            fprintf(stderr, "ERROR: fread failed at %llu\n", (unsigned long long)pos);
            return 1;
        }
        size_t n = to_read / BLK_SIZE;
        for (size_t i = 0; i < n; ++i) {
            uint16_t iz = (uint16_t)is_zero_4k(win + i * BLK_SIZE);
            if (iz == cur_is_zero) {
                cur_cnt++;
            } else {
                if (cur_is_zero != 0xFFFFU) {
                    EMIT_RUN(cur_is_zero, cur_cnt, cur_off);
                }
                cur_is_zero = iz;
                cur_cnt = 1;
                cur_off = pos + i * BLK_SIZE;
            }
        }
        pos += to_read;
    }
    if (cur_is_zero != 0xFFFFU) {
        EMIT_RUN(cur_is_zero, cur_cnt, cur_off);
    }
#undef EMIT_RUN

    uint64_t sparse_size = FILE_HDR_SZ;
    uint64_t raw_blks = 0, fill_blks = 0, dc_blks = 0;
    for (size_t i = 0; i < nchunks; ++i) {
        if (chunks[i].count == 0 || chunks[i].count > MAX_CHUNK_BLKS) {
            fprintf(stderr, "ERROR: chunk %zu count %u exceeds MAX_CHUNK_BLKS!\n", i, chunks[i].count);
            return 1;
        }
        if (chunks[i].type == CHUNK_TYPE_RAW) {
            sparse_size += CHUNK_HDR_SZ + (uint64_t)chunks[i].count * BLK_SIZE;
            raw_blks += chunks[i].count;
        } else if (chunks[i].type == CHUNK_TYPE_FILL) {
            sparse_size += CHUNK_HDR_SZ + 4ULL;
            fill_blks += chunks[i].count;
        } else {
            sparse_size += CHUNK_HDR_SZ;
            dc_blks += chunks[i].count;
        }
    }
    if (raw_blks + fill_blks + dc_blks != total_blks) {
        fprintf(stderr, "ERROR: block sum mismatch!\n");
        return 1;
    }
    printf("[scan] total_blks=%u | raw=%llu (%.2f GiB) | fill0=%llu (%.2f GiB) | dont_care=%llu (%.2f GiB) | chunks=%zu (max %u blks) | sparse_size=%llu (0x%llx)\n",
           total_blks,
           (unsigned long long)raw_blks, (double)(raw_blks * BLK_SIZE) / (1024.0 * 1024.0 * 1024.0),
           (unsigned long long)fill_blks, (double)(fill_blks * BLK_SIZE) / (1024.0 * 1024.0 * 1024.0),
           (unsigned long long)dc_blks, (double)(dc_blks * BLK_SIZE) / (1024.0 * 1024.0 * 1024.0),
           nchunks, MAX_CHUNK_BLKS,
           (unsigned long long)sparse_size, (unsigned long long)sparse_size);
    fflush(stdout);

    FILE *zf = fopen(lz4_path, "wb");
    if (!zf) {
        fprintf(stderr, "ERROR: cannot open %s for writing\n", lz4_path);
        return 1;
    }
    static char zf_iobuf[4 * 1024 * 1024];
    setvbuf(zf, zf_iobuf, _IOFBF, sizeof(zf_iobuf));

    LZ4F_compressionContext_t ctx;
    if (LZ4F_isError(LZ4F_createCompressionContext(&ctx, LZ4F_VERSION))) {
        fprintf(stderr, "ERROR: LZ4F_createCompressionContext failed\n");
        return 1;
    }

    LZ4F_preferences_t prefs;
    memset(&prefs, 0, sizeof(prefs));
    prefs.frameInfo.blockSizeID = LZ4F_max1MB;
    prefs.frameInfo.blockMode = LZ4F_blockIndependent;
    prefs.frameInfo.contentChecksumFlag = LZ4F_contentChecksumEnabled;
    prefs.frameInfo.contentSize = sparse_size;  /* Full 64-bit unsigned long long! */
    prefs.compressionLevel = 0;

    const size_t SRC_CHUNK = 4 * 1024 * 1024;
    size_t dst_cap = LZ4F_compressBound(SRC_CHUNK, &prefs) + 65536;
    uint8_t *dst_buf = (uint8_t *)malloc(dst_cap);

    size_t hdr_len = LZ4F_compressBegin(ctx, dst_buf, dst_cap, &prefs);
    if (LZ4F_isError(hdr_len) || fwrite(dst_buf, 1, hdr_len, zf) != hdr_len) {
        fprintf(stderr, "ERROR: LZ4F_compressBegin failed\n");
        return 1;
    }

    sparse_header_t shdr = {
        SPARSE_HEADER_MAGIC, 1, 0,
        (uint16_t)FILE_HDR_SZ, (uint16_t)CHUNK_HDR_SZ,
        (uint32_t)BLK_SIZE, total_blks, (uint32_t)nchunks, 0
    };
    if (lz4_write_checked(zf, ctx, dst_buf, dst_cap, &shdr, sizeof(shdr)) != 0)
        return 1;

    for (size_t i = 0; i < nchunks; ++i) {
        if (chunks[i].type == CHUNK_TYPE_RAW) {
            uint32_t data_sz = chunks[i].count * (uint32_t)BLK_SIZE;
            chunk_header_t ch = {CHUNK_TYPE_RAW, 0, chunks[i].count, (uint32_t)CHUNK_HDR_SZ + data_sz};
            if (lz4_write_checked(zf, ctx, dst_buf, dst_cap, &ch, sizeof(ch)) != 0)
                return 1;
            _fseeki64(rf, (int64_t)chunks[i].offset, SEEK_SET);
            uint32_t rem = data_sz;
            while (rem > 0) {
                uint32_t step = (rem > SRC_CHUNK) ? (uint32_t)SRC_CHUNK : rem;
                if (fread(win, 1, step, rf) != step) {
                    fprintf(stderr, "ERROR: fread chunk failed\n");
                    return 1;
                }
                if (lz4_write_checked(zf, ctx, dst_buf, dst_cap, win, step) != 0)
                    return 1;
                rem -= step;
            }
        } else if (chunks[i].type == CHUNK_TYPE_FILL) {
            struct {
                chunk_header_t ch;
                uint32_t fill_val;
            } pkt = {{CHUNK_TYPE_FILL, 0, chunks[i].count, (uint32_t)CHUNK_HDR_SZ + 4U}, 0U};
            if (lz4_write_checked(zf, ctx, dst_buf, dst_cap, &pkt, sizeof(pkt)) != 0)
                return 1;
        } else {
            chunk_header_t ch = {CHUNK_TYPE_DONT_CARE, 0, chunks[i].count, (uint32_t)CHUNK_HDR_SZ};
            if (lz4_write_checked(zf, ctx, dst_buf, dst_cap, &ch, sizeof(ch)) != 0)
                return 1;
        }
    }

    size_t end_len = LZ4F_compressEnd(ctx, dst_buf, dst_cap, NULL);
    if (LZ4F_isError(end_len)) {
        fprintf(stderr, "ERROR: LZ4F_compressEnd failed: %s\n", LZ4F_getErrorName(end_len));
        return 1;
    }
    if (fwrite(dst_buf, 1, end_len, zf) != end_len || fclose(zf) != 0) {
        fprintf(stderr, "ERROR: final fwrite/fclose failed\n");
        return 1;
    }
    LZ4F_freeCompressionContext(ctx);
    fclose(rf);
    free(win);
    free(dst_buf);
    free(chunks);

    FILE *chk = fopen(lz4_path, "rb");
    _fseeki64(chk, 0, SEEK_END);
    int64_t out_sz = _ftelli64(chk);
    fclose(chk);
    printf("[lz4] Wrote %s: %lld bytes (%.2f GiB)\n",
           lz4_path, (long long)out_sz, (double)out_sz / (1024.0 * 1024.0 * 1024.0));
    fflush(stdout);

    if (out_sz >= (1LL << 33)) {
        fprintf(stderr, "ERROR: %s exceeds 8 GiB USTAR limit!\n", lz4_path);
        return 1;
    }

    if (verify_lz4_frame(lz4_path, sparse_size) != 0) {
        return 1;
    }
    return 0;
}
