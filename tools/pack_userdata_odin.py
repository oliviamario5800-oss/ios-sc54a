#!/usr/bin/env python3
"""Convert raw 16 GiB HFS+ image into Samsung Odin USERDATA package (userdata.tar.md5).

Pipeline:
  raw HFS+ image (16 GiB)
    -> Android Sparse Image (magic 0xed26ff3a, 4096-byte blocks:
         non-zero runs -> CHUNK_TYPE_RAW  (0xCAC1)
         zero runs     -> CHUNK_TYPE_FILL (0xCAC2, fill=0x00000000)
       Using CHUNK_TYPE_FILL(0) rather than DONT_CARE guarantees Samsung Download Mode
       overwrites any stale Android f2fs blocks within the 16 GiB HFS+ volume with zeros
       while occupying only 16 bytes per zero run!)
    -> Standard LZ4 Frame (userdata.img.lz4, block_size=1MB, block_linked=False,
       content_checksum=True, content_size=sparse_total_size, matching Samsung stock .img.lz4)
    -> POSIX USTAR tar archive (userdata.tar) + Odin MD5 footer -> userdata.tar.md5
"""
import hashlib, os, struct, sys
import lz4.frame

SPARSE_HEADER_MAGIC = 0xed26ff3a
CHUNK_TYPE_RAW = 0xCAC1
CHUNK_TYPE_FILL = 0xCAC2
CHUNK_TYPE_DONT_CARE = 0xCAC3

BLK_SIZE = 4096
FILE_HDR_SZ = 28
CHUNK_HDR_SZ = 12
ZERO_BLK = b"\x00" * BLK_SIZE

# Max blocks per RAW chunk so memory usage stays small and chunks stay well under uint32 limits
MAX_RAW_BLKS = 16384  # 64 MiB


def scan_chunks(raw_path):
    """First pass: scan raw image in 64 MiB windows and build compact chunk list [(ctype, nblks, file_off), ...]."""
    fsize = os.path.getsize(raw_path)
    assert fsize % BLK_SIZE == 0, f"Image size {fsize} not a multiple of {BLK_SIZE}"
    total_blks = fsize // BLK_SIZE

    chunks = []  # (CHUNK_TYPE_RAW | CHUNK_TYPE_FILL, count_blks, byte_offset)
    cur_type = None
    cur_cnt = 0
    cur_off = 0

    WIN = 16384 * BLK_SIZE  # 64 MiB
    with open(raw_path, "rb") as f:
        pos = 0
        while pos < fsize:
            buf = f.read(min(WIN, fsize - pos))
            mv = memoryview(buf)
            n = len(buf) // BLK_SIZE
            for i in range(n):
                blk = mv[i * BLK_SIZE:(i + 1) * BLK_SIZE]
                is_zero = (blk == ZERO_BLK)
                t = CHUNK_TYPE_FILL if is_zero else CHUNK_TYPE_RAW
                if t == cur_type and (t == CHUNK_TYPE_FILL or cur_cnt < MAX_RAW_BLKS):
                    cur_cnt += 1
                else:
                    if cur_type is not None:
                        chunks.append((cur_type, cur_cnt, cur_off))
                    cur_type = t
                    cur_cnt = 1
                    cur_off = pos + i * BLK_SIZE
            pos += len(buf)

    if cur_type is not None:
        chunks.append((cur_type, cur_cnt, cur_off))

    sparse_size = FILE_HDR_SZ
    raw_blks = 0
    fill_blks = 0
    for ctype, cnt, _ in chunks:
        if ctype == CHUNK_TYPE_RAW:
            sparse_size += CHUNK_HDR_SZ + cnt * BLK_SIZE
            raw_blks += cnt
        else:
            sparse_size += CHUNK_HDR_SZ + 4
            fill_blks += cnt

    assert raw_blks + fill_blks == total_blks
    print(f"[scan] total_blks={total_blks} ({fsize / (1024**3):.2f} GiB) | "
          f"raw_blks={raw_blks} ({raw_blks * BLK_SIZE / (1024**3):.2f} GiB) | "
          f"zero_fill_blks={fill_blks} ({fill_blks * BLK_SIZE / (1024**3):.2f} GiB) | "
          f"chunks={len(chunks)} | sparse_size={sparse_size} bytes")
    return total_blks, chunks, sparse_size


def write_sparse_lz4(raw_path, out_lz4_path, total_blks, chunks, sparse_size):
    """Second pass: stream Android Sparse Image directly into Samsung-compatible LZ4 frame."""
    file_hdr = struct.pack(
        "<IHHHHIIII",
        SPARSE_HEADER_MAGIC,
        1,              # major_version
        0,              # minor_version
        FILE_HDR_SZ,    # file_hdr_sz (28)
        CHUNK_HDR_SZ,   # chunk_hdr_sz (12)
        BLK_SIZE,       # blk_sz (4096)
        total_blks,     # total_blks
        len(chunks),    # total_chunks
        0,              # image_checksum
    )
    assert len(file_hdr) == FILE_HDR_SZ

    written_uncompressed = 0
    with open(raw_path, "rb") as rf, lz4.frame.open(
        out_lz4_path,
        mode="wb",
        block_size=lz4.frame.BLOCKSIZE_MAX1MB,
        block_linked=False,
        compression_level=0,
        content_checksum=True,
        source_size=sparse_size,
    ) as zf:
        zf.write(file_hdr)
        written_uncompressed += len(file_hdr)

        for idx, (ctype, cnt, off) in enumerate(chunks):
            if ctype == CHUNK_TYPE_RAW:
                data_sz = cnt * BLK_SIZE
                total_sz = CHUNK_HDR_SZ + data_sz
                chdr = struct.pack("<HHII", CHUNK_TYPE_RAW, 0, cnt, total_sz)
                zf.write(chdr)
                rf.seek(off)
                rem = data_sz
                while rem > 0:
                    step = min(rem, 4 * 1024 * 1024)
                    d = rf.read(step)
                    assert len(d) == step
                    zf.write(d)
                    rem -= step
                written_uncompressed += total_sz
            else:
                total_sz = CHUNK_HDR_SZ + 4
                chdr = struct.pack("<HHIII", CHUNK_TYPE_FILL, 0, cnt, total_sz, 0)
                zf.write(chdr)
                written_uncompressed += total_sz

            if (idx + 1) % 500 == 0 or idx + 1 == len(chunks):
                pct = written_uncompressed * 100.0 / sparse_size
                print(f"  [lz4] chunk {idx + 1}/{len(chunks)} ({pct:.1f}%)")

    assert written_uncompressed == sparse_size
    lz4_sz = os.path.getsize(out_lz4_path)
    print(f"[lz4] Wrote {out_lz4_path}: {lz4_sz} bytes ({lz4_sz / (1024**3):.2f} GiB)")
    if lz4_sz >= (1 << 33):
        raise RuntimeError(f"userdata.img.lz4 ({lz4_sz} bytes) exceeds 8 GiB USTAR limit!")
    return lz4_sz


def make_ustar_header(arcname, size, mtime=0):
    assert len(arcname.encode("ascii")) <= 99
    assert size < (1 << 33), "Size >= 8 GiB does not fit in standard 11-octal-digit USTAR!"
    h = bytearray(512)
    name_b = arcname.encode("ascii")
    h[0:len(name_b)] = name_b
    h[100:108] = b"0000644\x00"
    h[108:116] = b"0000000\x00"
    h[116:124] = b"0000000\x00"
    h[124:136] = f"{size:011o}\x00".encode("ascii")
    h[136:148] = f"{int(mtime):011o}\x00".encode("ascii")
    h[148:156] = b"        "
    h[156] = ord("0")
    h[257:263] = b"ustar\x00"
    h[263:265] = b"00"
    h[265:269] = b"root"
    h[297:301] = b"root"
    h[329:337] = b"0000000\x00"
    h[337:345] = b"0000000\x00"
    chksum = sum(h)
    h[148:156] = f"{chksum:06o}\x00 ".encode("ascii")
    return bytes(h)


def write_odin_tar_md5(lz4_path, out_tar_md5_path, arcname="userdata.img.lz4", tar_basename="userdata.tar"):
    """Stream USTAR tar directly to out_tar_md5_path while computing MD5 on the fly."""
    lz4_sz = os.path.getsize(lz4_path)
    mtime = int(os.path.getmtime(lz4_path))
    hdr = make_ustar_header(arcname, lz4_sz, mtime)
    md5 = hashlib.md5()

    with open(lz4_path, "rb") as src, open(out_tar_md5_path, "wb") as dst:
        dst.write(hdr)
        md5.update(hdr)

        copied = 0
        while copied < lz4_sz:
            buf = src.read(8 * 1024 * 1024)
            if not buf:
                break
            dst.write(buf)
            md5.update(buf)
            copied += len(buf)
        assert copied == lz4_sz

        rem = lz4_sz % 512
        if rem:
            pad = b"\x00" * (512 - rem)
            dst.write(pad)
            md5.update(pad)

        eof = b"\x00" * 1024
        dst.write(eof)
        md5.update(eof)

        md5_hex = md5.hexdigest()
        footer = f"{md5_hex}  {tar_basename}\n".encode("ascii")
        dst.write(footer)

    final_sz = os.path.getsize(out_tar_md5_path)
    print(f"[odin] Wrote {out_tar_md5_path}: {final_sz} bytes ({final_sz / (1024**3):.2f} GiB), tar MD5={md5_hex}")


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <raw_hfs.img> <temp_userdata.img.lz4> <out_userdata.tar.md5>")
        sys.exit(2)
    raw_path, lz4_path, tar_md5_path = sys.argv[1], sys.argv[2], sys.argv[3]
    import subprocess
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    simg2lz4_exe = os.path.join(tools_dir, "simg2lz4.exe")
    if not os.path.exists(simg2lz4_exe):
        zig = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"
        lz4_lib = os.path.join(os.path.dirname(tools_dir), "research", "apfs-src", "lz4-1.10.0", "lib")
        subprocess.run([
            zig, "cc", "-O3", f"-I{lz4_lib}",
            os.path.join(tools_dir, "simg2lz4.c"),
            os.path.join(lz4_lib, "lz4.c"),
            os.path.join(lz4_lib, "lz4hc.c"),
            os.path.join(lz4_lib, "lz4frame.c"),
            os.path.join(lz4_lib, "xxhash.c"),
            "-o", simg2lz4_exe,
        ], check=True)
    subprocess.run([simg2lz4_exe, raw_path, lz4_path], check=True)
    write_odin_tar_md5(lz4_path, tar_md5_path)


if __name__ == "__main__":
    main()
