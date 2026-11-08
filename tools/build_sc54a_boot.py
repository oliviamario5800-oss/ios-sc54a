#!/usr/bin/env python3
"""End-to-end native Windows builder for SC-54A XNU boot.img and Odin boot.tar.md5."""
import hashlib, os, shutil, struct, subprocess, sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TOOLS_DIR)
SHIM_DIR = os.path.join(PROJ_DIR, "shim")
OUT_DIR = os.path.join(SHIM_DIR, "out")
STOCK_DIR = os.path.join(PROJ_DIR, "stock")
XNU_KERNEL = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a")
ZIG = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"
DESKTOP_TAR_MD5 = r"C:\Users\user\Desktop\boot.tar.md5"

sys.path.insert(0, TOOLS_DIR)
import mkadt, adt, mkpayload, repack_bootimg  # noqa: E402

CMDLINE = (
    "debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress "
    "rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin "
    "ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 "
    "sweetfb_noconsole=1 sweetfb_hz=30 sweetkeys=1 sweettouch=0 sweetbattery=0 sweetusb=0"
)


def run(cmd, cwd=None):
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def check_no_abs64_relocs(obj_paths):
    """Verify 0 R_AARCH64_ABS64 (257) and 0 GOT relocations in compiled C objects."""
    SHT_RELA = 4
    R_AARCH64_ABS64 = 257
    GOT_RELOCS = {307, 311, 312, 313}  # ADR_GOT_PAGE, LD64_GOT_LO12_NC, LD64_GOTOFF_LO15, LD64_GOTPAGE_LO15
    for p in obj_paths:
        b = open(p, "rb").read()
        assert b[:4] == b"\x7fELF" and b[4] == 2
        e_shoff, = struct.unpack_from("<Q", b, 40)
        e_shentsize, e_shnum = struct.unpack_from("<HH", b, 58)
        for i in range(e_shnum):
            sh = b[e_shoff + i * e_shentsize:e_shoff + (i + 1) * e_shentsize]
            sh_type, = struct.unpack_from("<I", sh, 4)
            if sh_type == SHT_RELA:
                sh_offset, sh_size = struct.unpack_from("<QQ", sh, 24)
                for off in range(sh_offset, sh_offset + sh_size, 24):
                    _, r_info, _ = struct.unpack_from("<QQq", b, off)
                    r_type = r_info & 0xffffffff
                    if r_type == R_AARCH64_ABS64 or r_type in GOT_RELOCS:
                        raise RuntimeError(f"Found non-PC-relative relocation type {r_type} in {p}!")


def elf_to_flat_image(elf_path):
    """Extract PT_LOAD segments at p_paddr (linked at 0) and read _end from .symtab."""
    b = open(elf_path, "rb").read()
    assert b[:4] == b"\x7fELF" and b[4] == 2
    e_phoff, e_shoff = struct.unpack_from("<QQ", b, 32)
    e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHHHH", b, 54)

    PT_LOAD = 1
    loads = []
    max_file_end = 0
    for i in range(e_phnum):
        ph = b[e_phoff + i * e_phentsize:e_phoff + (i + 1) * e_phentsize]
        p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack("<IIQQQQQQ", ph)
        if p_type == PT_LOAD and p_filesz > 0:
            loads.append((p_paddr, b[p_offset:p_offset + p_filesz]))
            max_file_end = max(max_file_end, p_paddr + p_filesz)

    img = bytearray(max_file_end)
    for paddr, data in loads:
        img[paddr:paddr + len(data)] = data

    # Find _end symbol value in SHT_SYMTAB (type 2)
    SHT_SYMTAB = 2
    sym_end = None
    for i in range(e_shnum):
        sh = b[e_shoff + i * e_shentsize:e_shoff + (i + 1) * e_shentsize]
        sh_type, = struct.unpack_from("<I", sh, 4)
        if sh_type == SHT_SYMTAB:
            sh_offset, sh_size = struct.unpack_from("<QQ", sh, 24)
            sh_link, = struct.unpack_from("<I", sh, 40)
            str_sh = b[e_shoff + sh_link * e_shentsize:e_shoff + (sh_link + 1) * e_shentsize]
            str_off, str_sz = struct.unpack_from("<QQ", str_sh, 24)
            strtab = b[str_off:str_off + str_sz]
            for off in range(sh_offset, sh_offset + sh_size, 24):
                st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", b, off)
                end_idx = strtab.find(b"\0", st_name)
                name = strtab[st_name:end_idx].decode("ascii", errors="replace")
                if name == "_end":
                    sym_end = st_value
    assert sym_end is not None, "_end symbol not found in shim.elf"
    return img, sym_end


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1. Build ADT
    adt_path = os.path.join(OUT_DIR, "sc54a.adt")
    root = mkadt.build(qemu=False, highmem=True)
    adt_blob = adt.serialize(root)
    open(adt_path, "wb").write(adt_blob)
    print(f"[1/5] Built ADT: {adt_path} ({len(adt_blob)} bytes)")

    # 2. Build XNU payload.bin
    payload_path = os.path.join(OUT_DIR, "payload.bin")
    payload_blob, info = mkpayload.build(XNU_KERNEL, adt_path, CMDLINE)
    open(payload_path, "wb").write(payload_blob)
    print(f"[2/5] Built XNU payload: {payload_path} ({len(payload_blob):#x} bytes, entry={info['entry']})")

    # 3. Compile bare-metal ARM64 shim via Zig CC
    cflags = [
        ZIG, "cc", "-target", "aarch64-freestanding-none", "-mcpu=cortex_a55",
        "-O2", "-g0", "-ffreestanding", "-nostdlib", "-fno-pic", "-fno-jump-tables",
        "-mgeneral-regs-only", "-mstrict-align", "-fno-stack-protector",
        "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
        "-Wall", "-Wextra",
    ]
    sources = ["start.S", "shim.c", "console.c", "mmu.c", "fdt.c", "loader.c", "ufs.c"]
    objs = []
    c_objs = []
    for src in sources:
        stem = os.path.splitext(src)[0]
        obj = os.path.join(OUT_DIR, f"{stem}.o")
        run(cflags + ["-c", os.path.join(SHIM_DIR, src), "-o", obj], cwd=SHIM_DIR)
        objs.append(obj)
        if src.endswith(".c"):
            c_objs.append(obj)

    check_no_abs64_relocs(c_objs)

    elf_path = os.path.join(OUT_DIR, "shim.elf")
    run([
        ZIG, "cc", "-target", "aarch64-freestanding-none",
        "-nostdlib", "-static",
        "-Wl,-T,shim.ld", "-Wl,--build-id=none",
        *objs, "-o", elf_path,
    ], cwd=SHIM_DIR)

    shim_bin, sym_end = elf_to_flat_image(elf_path)
    emb_off = (sym_end + 0xfff) & ~0xfff
    assert len(shim_bin) <= emb_off, f"shim file bytes {len(shim_bin):#x} > _end aligned {emb_off:#x}"
    shim_bin.extend(b"\x00" * (emb_off - len(shim_bin)))

    # Append XNU payload.bin right at emb_off
    shim_bin.extend(payload_blob)

    # CRITICAL FIX (Root Cause #1): Update ARM64 Image _head.image_size at offset 0x10
    # so Samsung/Qualcomm ABL allocates the FULL ~17 MiB buffer for shim + payload.bin!
    full_img_size = (len(shim_bin) + 0x3ffff) & ~0x3ffff
    old_img_size, = struct.unpack_from("<Q", shim_bin, 0x10)
    struct.pack_into("<Q", shim_bin, 0x10, full_img_size)
    assert shim_bin[0x38:0x3c] == b"ARM\x64", "bad ARM64 Image magic"

    image_path = os.path.join(OUT_DIR, "Image")
    open(image_path, "wb").write(shim_bin)
    print(f"[3/5] Built ARM64 Image: {image_path} (len={len(shim_bin):#x}, _end={sym_end:#x}, emb_off={emb_off:#x}, _head.image_size: {old_img_size:#x} -> {full_img_size:#x})")

    # 4. Repack Samsung 96 MiB boot.img
    stock_boot = os.path.join(STOCK_DIR, "boot.img")
    stock_dtb = os.path.join(STOCK_DIR, "unpacked", "dtb")
    stock_ramdisk = os.path.join(STOCK_DIR, "ramdisk.cpio.gz")
    out_boot = os.path.join(OUT_DIR, "magisk-boot.img")
    repack_bootimg.repack(
        stock_boot, image_path, out_boot,
        ramdisk_path=stock_ramdisk if os.path.exists(stock_ramdisk) else None,
        dtb_path=stock_dtb,
        patch_vbmeta=True,
    )
    print(f"[4/5] Built Samsung boot.img: {out_boot}")

    # 5. Package Odin boot.tar.md5 on Desktop
    staging_boot = os.path.join(OUT_DIR, "boot.img")
    staging_tar = os.path.join(OUT_DIR, "boot.tar")
    shutil.copyfile(out_boot, staging_boot)
    run([r"C:\Windows\System32\tar.exe", "-cf", staging_tar, "--format", "ustar", "-C", OUT_DIR, "boot.img"])
    tar_bytes = open(staging_tar, "rb").read()
    md5_hex = hashlib.md5(tar_bytes).hexdigest()
    footer = f"{md5_hex}  boot.tar\n".encode("ascii")
    with open(DESKTOP_TAR_MD5, "wb") as f:
        f.write(tar_bytes)
        f.write(footer)
    print(f"[5/5] Packaged Odin AP archive: {DESKTOP_TAR_MD5} ({len(tar_bytes) + len(footer)} bytes, tar MD5={md5_hex})")


if __name__ == "__main__":
    main()
