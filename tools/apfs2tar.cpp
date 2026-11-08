/*
 * Native APFS DMG -> POSIX/GNU uncompressed TAR extractor for ios-sc54a.
 * Uses ApfsLib (supporting raw APFS + UDIF DMG + decmpfs LZBITMAP/LZFSE/LZVN/ZLIB)
 * to extract and decompress iOS 16 IPSW images at native CPU speed while preserving
 * exact uid, gid, mode, mtime, symlinks, and hardlinks.
 */
#include <ApfsLib/ApfsContainer.h>
#include <ApfsLib/ApfsVolume.h>
#include <ApfsLib/ApfsDir.h>
#include <ApfsLib/Decmpfs.h>
#include <ApfsLib/Device.h>
#include <ApfsLib/GptPartitionMap.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cinttypes>
#include <string>
#include <unordered_map>
#include <vector>

struct TarHeader {
    char name[100];
    char mode[8];
    char uid[8];
    char gid[8];
    char size[12];
    char mtime[12];
    char chksum[8];
    char typeflag;
    char linkname[100];
    char magic[6];
    char version[2];
    char uname[32];
    char gname[32];
    char devmajor[8];
    char devminor[8];
    char prefix[155];
    char pad[12];
};
static_assert(sizeof(TarHeader) == 512, "TarHeader must be 512 bytes");

static void encode_octal_or_base256(char *dst, size_t width, uint64_t val) {
    uint64_t max_oct = (1ULL << ((width - 1) * 3)) - 1;
    if (val <= max_oct) {
        char fmt[16];
        snprintf(fmt, sizeof(fmt), "%%0%zullo", width - 1);
        snprintf(dst, width, fmt, (unsigned long long)val);
    } else {
        memset(dst, 0, width);
        dst[0] = (char)0x80;
        for (size_t i = 0; i < 8 && i + 1 < width; ++i) {
            dst[width - 1 - i] = (char)((val >> (i * 8)) & 0xff);
        }
    }
}

static bool write_all(FILE *fp, const void *buf, size_t len) {
    return len == 0 || fwrite(buf, 1, len, fp) == len;
}

static bool write_pad512(FILE *fp, uint64_t size) {
    size_t rem = (size_t)(size & 511ULL);
    if (rem == 0) return true;
    static const uint8_t zeros[512] = {0};
    return write_all(fp, zeros, 512 - rem);
}

static bool emit_raw_header(FILE *fp, const char *name_field, char typeflag,
                            uint32_t mode, uint32_t uid, uint32_t gid,
                            uint64_t size, uint64_t mtime_sec,
                            const char *link_field, uint32_t devmajor, uint32_t devminor) {
    TarHeader h;
    memset(&h, 0, sizeof(h));
    strncpy(h.name, name_field, sizeof(h.name));
    encode_octal_or_base256(h.mode, sizeof(h.mode), mode & 07777);
    encode_octal_or_base256(h.uid, sizeof(h.uid), uid);
    encode_octal_or_base256(h.gid, sizeof(h.gid), gid);
    encode_octal_or_base256(h.size, sizeof(h.size), size);
    encode_octal_or_base256(h.mtime, sizeof(h.mtime), mtime_sec);
    memset(h.chksum, ' ', sizeof(h.chksum));
    h.typeflag = typeflag;
    if (link_field) {
        strncpy(h.linkname, link_field, sizeof(h.linkname));
    }
    memcpy(h.magic, "ustar ", 6);
    h.version[0] = ' ';
    h.version[1] = '\0';
    if (typeflag == '3' || typeflag == '4') {
        encode_octal_or_base256(h.devmajor, sizeof(h.devmajor), devmajor);
        encode_octal_or_base256(h.devminor, sizeof(h.devminor), devminor);
    }
    uint32_t sum = 0;
    const uint8_t * raw = reinterpret_cast<const uint8_t *>(&h);
    for (size_t i = 0; i < sizeof(h); ++i) sum += raw[i];
    snprintf(h.chksum, 7, "%06o", sum);
    h.chksum[7] = ' ';
    return write_all(fp, &h, sizeof(h));
}

static bool emit_gnu_long(FILE *fp, char typeflag, const std::string &str) {
    uint64_t len = str.size() + 1;
    if (!emit_raw_header(fp, "././@LongLink", typeflag, 0, 0, 0, len, 0, nullptr, 0, 0))
        return false;
    if (!write_all(fp, str.c_str(), (size_t)len))
        return false;
    return write_pad512(fp, len);
}

static bool emit_tar_header(FILE *fp, const std::string &path, char typeflag,
                            uint32_t mode, uint32_t uid, uint32_t gid,
                            uint64_t size, uint64_t mtime_sec,
                            const std::string &linkpath,
                            uint32_t devmajor = 0, uint32_t devminor = 0) {
    if (linkpath.size() > 99) {
        if (!emit_gnu_long(fp, 'K', linkpath)) return false;
    }
    if (path.size() > 99) {
        if (!emit_gnu_long(fp, 'L', path)) return false;
    }
    std::string short_name = path.substr(0, 99);
    std::string short_link = linkpath.substr(0, 99);
    return emit_raw_header(fp, short_name.c_str(), typeflag, mode, uid, gid,
                           size, mtime_sec,
                           linkpath.empty() ? nullptr : short_link.c_str(),
                           devmajor, devminor);
}

struct Stats {
    uint64_t dirs = 0;
    uint64_t regs = 0;
    uint64_t compressed = 0;
    uint64_t symlinks = 0;
    uint64_t hardlinks = 0;
    uint64_t specials = 0;
    uint64_t total_bytes = 0;
};

struct Extractor {
    ApfsDir &dir;
    FILE *fp;
    std::unordered_map<uint64_t, std::string> seen_inodes;
    Stats st;

    bool walk(uint64_t dir_ino, const std::string &rel_prefix) {
        std::vector<ApfsDir::DirRec> entries;
        if (!dir.ListDirectory(entries, dir_ino)) {
            fprintf(stderr, "ERROR: ListDirectory failed on inode %" PRIu64 " (%s)\n",
                    dir_ino, rel_prefix.c_str());
            return false;
        }

        for (const auto &e : entries) {
            std::string path = rel_prefix.empty() ? e.name : (rel_prefix + "/" + e.name);
            uint64_t ino = e.file_id;
            ApfsDir::Inode rec;
            if (!dir.GetInode(rec, ino)) {
                fprintf(stderr, "ERROR: GetInode failed on inode %" PRIu64 " (%s)\n",
                        ino, path.c_str());
                return false;
            }

            uint16_t ftype = rec.mode & MODE_S_IFMT;
            uint64_t mtime_sec = rec.mod_time / 1000000000ULL;

            if (ftype == MODE_S_IFDIR) {
                std::string dpath = path + "/";
                if (!emit_tar_header(fp, dpath, '5', rec.mode, rec.owner, rec.group, 0, mtime_sec, ""))
                    return false;
                st.dirs++;
                if (!walk(ino, path))
                    return false;
                continue;
            }

            // Hardlink detection for non-directory inodes
            auto it = seen_inodes.find(ino);
            if (it != seen_inodes.end()) {
                if (!emit_tar_header(fp, path, '1', rec.mode, rec.owner, rec.group, 0, mtime_sec, it->second))
                    return false;
                st.hardlinks++;
                continue;
            }
            seen_inodes[ino] = path;

            if (ftype == MODE_S_IFLNK) {
                std::vector<uint8_t> target_raw;
                if (!dir.GetAttribute(target_raw, ino, "com.apple.fs.symlink")) {
                    fprintf(stderr, "ERROR: symlink missing com.apple.fs.symlink on inode %" PRIu64 " (%s)\n",
                            ino, path.c_str());
                    return false;
                }
                size_t tlen = target_raw.size();
                while (tlen > 0 && target_raw[tlen - 1] == 0) tlen--;
                std::string target(reinterpret_cast<const char *>(target_raw.data()), tlen);
                if (!emit_tar_header(fp, path, '2', rec.mode, rec.owner, rec.group, 0, mtime_sec, target))
                    return false;
                st.symlinks++;
            } else if (ftype == MODE_S_IFREG) {
                if (rec.bsd_flags & APFS_UF_COMPRESSED) {
                    std::vector<uint8_t> attr;
                    if (!dir.GetAttribute(attr, ino, "com.apple.decmpfs")) {
                        fprintf(stderr, "ERROR: compressed file missing com.apple.decmpfs on inode %" PRIu64 " (%s)\n",
                                ino, path.c_str());
                        return false;
                    }
                    std::vector<uint8_t> decomp;
                    if (!DecompressFile(dir, ino, decomp, attr)) {
                        fprintf(stderr, "ERROR: DecompressFile failed on inode %" PRIu64 " (%s)\n",
                                ino, path.c_str());
                        return false;
                    }
                    if (!emit_tar_header(fp, path, '0', rec.mode, rec.owner, rec.group, decomp.size(), mtime_sec, ""))
                        return false;
                    if (!write_all(fp, decomp.data(), decomp.size()))
                        return false;
                    if (!write_pad512(fp, decomp.size()))
                        return false;
                    st.regs++;
                    st.compressed++;
                    st.total_bytes += decomp.size();
                } else {
                    uint64_t fsize = (rec.optional_present_flags & ApfsDir::Inode::INO_HAS_DSTREAM) ? rec.ds_size : 0;
                    if (!emit_tar_header(fp, path, '0', rec.mode, rec.owner, rec.group, fsize, mtime_sec, ""))
                        return false;
                    if (fsize > 0) {
                        constexpr size_t CHUNK = 4 * 1024 * 1024;
                        std::vector<uint8_t> buf(CHUNK);
                        uint64_t off = 0;
                        while (off < fsize) {
                            size_t n = (fsize - off > CHUNK) ? CHUNK : (size_t)(fsize - off);
                            if (!dir.ReadFile(buf.data(), rec.private_id, off, n)) {
                                fprintf(stderr, "ERROR: ReadFile failed on inode %" PRIu64 " (%s) at off %" PRIu64 "\n",
                                        ino, path.c_str(), off);
                                return false;
                            }
                            if (!write_all(fp, buf.data(), n))
                                return false;
                            off += n;
                        }
                        if (!write_pad512(fp, fsize))
                            return false;
                    }
                    st.regs++;
                    st.total_bytes += fsize;
                }
            } else if (ftype == MODE_S_IFIFO) {
                if (!emit_tar_header(fp, path, '6', rec.mode, rec.owner, rec.group, 0, mtime_sec, ""))
                    return false;
                st.specials++;
            } else if (ftype == MODE_S_IFCHR || ftype == MODE_S_IFBLK) {
                char tf = (ftype == MODE_S_IFCHR) ? '3' : '4';
                uint32_t maj = (rec.rdev >> 24) & 0xff;
                uint32_t min = rec.rdev & 0xffffff;
                if (!emit_tar_header(fp, path, tf, rec.mode, rec.owner, rec.group, 0, mtime_sec, "", maj, min))
                    return false;
                st.specials++;
            }
        }
        return true;
    }
};

int main(int argc, char *argv[]) {
    if (argc < 3 || argc > 4) {
        fprintf(stderr, "Usage: %s <input.dmg> <output.tar> [prefix]\n", argv[0]);
        return 2;
    }
    const char *dmg_path = argv[1];
    const char *tar_path = argv[2];
    std::string prefix = (argc == 4) ? argv[3] : "";
    while (!prefix.empty() && prefix.back() == '/') prefix.pop_back();

    g_debug = Dbg_Errors;

    Device *dev = Device::OpenDevice(dmg_path);
    if (!dev) {
        fprintf(stderr, "ERROR: Unable to open DMG: %s\n", dmg_path);
        return 1;
    }

    uint64_t offset = 0;
    uint64_t size = dev->GetSize();
    GptPartitionMap gpt;
    if (gpt.LoadAndVerify(*dev)) {
        int part = gpt.FindFirstAPFSPartition();
        if (part >= 0) {
            gpt.GetPartitionOffsetAndSize(part, offset, size);
        }
    }

    ApfsContainer *container = new ApfsContainer(dev, offset, size);
    if (!container->Init()) {
        fprintf(stderr, "ERROR: ApfsContainer::Init failed for %s\n", dmg_path);
        return 1;
    }

    ApfsVolume *vol = container->GetVolume(0);
    if (!vol) {
        fprintf(stderr, "ERROR: GetVolume(0) failed for %s\n", dmg_path);
        return 1;
    }

    FILE *fp = fopen(tar_path, "wb");
    if (!fp) {
        fprintf(stderr, "ERROR: Cannot open output tar %s\n", tar_path);
        return 1;
    }
    // Use 4 MiB stdio buffer for fast sequential output
    static char io_buf[4 * 1024 * 1024];
    setvbuf(fp, io_buf, _IOFBF, sizeof(io_buf));

    ApfsDir dir(*vol);
    Extractor ex{dir, fp, {}, {}};

    // Emit parent directories if prefix is given
    if (!prefix.empty()) {
        size_t pos = 0;
        while (pos < prefix.size()) {
            size_t next = prefix.find('/', pos);
            std::string sub = (next == std::string::npos) ? prefix : prefix.substr(0, next);
            if (!emit_tar_header(fp, sub + "/", '5', 0755, 0, 0, 0, 0, "")) {
                fclose(fp);
                return 1;
            }
            if (next == std::string::npos) break;
            pos = next + 1;
        }
    }

    if (!ex.walk(ROOT_DIR_INO_NUM, prefix)) {
        fclose(fp);
        return 1;
    }

    // Two 512-byte zero blocks mark end-of-archive
    static const uint8_t eof_blocks[1024] = {0};
    if (!write_all(fp, eof_blocks, sizeof(eof_blocks))) {
        fprintf(stderr, "ERROR: Failed writing tar EOF blocks\n");
        fclose(fp);
        return 1;
    }
    if (fclose(fp) != 0) {
        fprintf(stderr, "ERROR: fclose failed on %s\n", tar_path);
        return 1;
    }

    printf("OK: %s -> %s | dirs=%" PRIu64 " regs=%" PRIu64 " (compressed=%" PRIu64 ") symlinks=%" PRIu64 " hardlinks=%" PRIu64 " specials=%" PRIu64 " bytes=%" PRIu64 " (%.1f MiB)\n",
           dmg_path, tar_path,
           ex.st.dirs, ex.st.regs, ex.st.compressed, ex.st.symlinks, ex.st.hardlinks, ex.st.specials,
           ex.st.total_bytes, (double)ex.st.total_bytes / (1024.0 * 1024.0));

    delete vol;
    delete container;
    dev->Close();
    delete dev;
    return 0;
}
