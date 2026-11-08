#!/usr/bin/env python3
"""Native Python Samsung/Android boot.img v2 repacker with SEANDROIDENFORCE + AVB0/AVBf.

Replicates `PATCHVBMETAFLAG=true magiskboot repack stock/boot.img out_boot.img`
byte-for-byte without requiring a Linux ELF binary or WSL.
"""
import argparse, hashlib, struct

PAGE = 0x1000
AVBF_SIZE = 64


def align(x, p=PAGE):
    return (x + p - 1) & ~(p - 1)


def repack(stock_boot_path, kernel_path, out_path, ramdisk_path=None, dtb_path=None, patch_vbmeta=True):
    stock = open(stock_boot_path, "rb").read()
    total_len = len(stock)
    assert stock[:8] == b"ANDROID!", "not an Android boot.img"

    (s_ksz, kaddr, s_rsz, raddr, s_ssz, saddr, tags, pgsz, hver, osver) = struct.unpack_from("<10I", stock, 8)
    assert pgsz == PAGE and hver == 2, f"unexpected pgsz={pgsz:#x} hver={hver}"
    rec_dtbo_sz, = struct.unpack_from("<I", stock, 1632)
    rec_dtbo_off, = struct.unpack_from("<Q", stock, 1636)
    hdr_sz, = struct.unpack_from("<I", stock, 1644)
    s_dtb_sz, = struct.unpack_from("<I", stock, 1648)

    s_k_off = pgsz
    s_r_off = s_k_off + align(s_ksz, pgsz)
    s_s_off = s_r_off + align(s_rsz, pgsz)
    s_rdtbo_off = s_s_off + align(s_ssz, pgsz)
    s_d_off = s_rdtbo_off + align(rec_dtbo_sz, pgsz)

    # Extract stock AVBf + AVB0
    avbf = stock[-AVBF_SIZE:]
    f_magic, f_maj, f_min, _, s_vb_off, s_vb_sz = struct.unpack_from(">4sIIQQQ", avbf, 0)
    assert f_magic == b"AVBf", "missing AVBf footer in stock boot.img"
    avb0 = bytearray(stock[s_vb_off:s_vb_off + s_vb_sz])
    assert avb0[:4] == b"AVB0", "missing AVB0 struct in stock boot.img"
    if patch_vbmeta:
        flags, = struct.unpack_from(">I", avb0, 120)
        struct.pack_into(">I", avb0, 120, flags | 0x3)

    kernel = open(kernel_path, "rb").read()
    ramdisk = open(ramdisk_path, "rb").read() if ramdisk_path else stock[s_r_off:s_r_off + s_rsz]
    second = stock[s_s_off:s_s_off + s_ssz]
    rec_dtbo = stock[s_rdtbo_off:s_rdtbo_off + rec_dtbo_sz]
    dtb = open(dtb_path, "rb").read() if dtb_path else stock[s_d_off:s_d_off + s_dtb_sz]

    hdr = bytearray(stock[:pgsz])
    struct.pack_into("<I", hdr, 8, len(kernel))
    struct.pack_into("<I", hdr, 16, len(ramdisk))
    struct.pack_into("<I", hdr, 1648, len(dtb))

    h = hashlib.sha1()
    for blob in (kernel, ramdisk, second, rec_dtbo, dtb):
        h.update(blob)
        h.update(struct.pack("<I", len(blob)))
    hdr[576:608] = h.digest() + b"\x00" * 12

    out = bytearray(total_len)
    pos = 0
    out[pos:pos + pgsz] = hdr
    pos += pgsz

    for blob in (kernel, ramdisk, second, rec_dtbo, dtb):
        if len(blob):
            out[pos:pos + len(blob)] = blob
            pos += align(len(blob), pgsz)

    # Samsung SEANDROIDENFORCE trailer + AVB0 on next page + AVBf at end of partition
    se_tag = b"SEANDROIDENFORCE"
    out[pos:pos + len(se_tag)] = se_tag
    orig_img_sz = pos + len(se_tag)
    vb_off = pos + pgsz
    assert vb_off + len(avb0) <= total_len - AVBF_SIZE, "boot.img overflow"
    out[vb_off:vb_off + len(avb0)] = avb0

    new_avbf = struct.pack(">4sIIQQQ28x", b"AVBf", f_maj, f_min, orig_img_sz, vb_off, len(avb0))
    out[-AVBF_SIZE:] = new_avbf

    open(out_path, "wb").write(out)
    print(f"Repacked {out_path}: total={total_len:#x} kernel={len(kernel):#x} ramdisk={len(ramdisk):#x} dtb={len(dtb):#x} sha1={h.hexdigest()} vb_off={vb_off:#x}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stock_boot")
    ap.add_argument("kernel")
    ap.add_argument("out_boot")
    ap.add_argument("--ramdisk")
    ap.add_argument("--dtb")
    a = ap.parse_args()
    repack(a.stock_boot, a.kernel, a.out_boot, a.ramdisk, a.dtb)
