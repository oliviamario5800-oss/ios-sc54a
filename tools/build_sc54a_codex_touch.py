import subprocess, struct, os, sys, shutil, re

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TOOLS_DIR)
ZIG = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"
KERNEL_GOOD = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a.GOOD")
KERNEL_TARGET = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a")
CODEX_SRC = r"C:\Users\user\Documents\Codex\2026-11-01\k\outputs\sc54a_mms_touch_driver.c"

def main():
    print(f"[1/5] Checking source driver: {CODEX_SRC}")
    assert os.path.isfile(CODEX_SRC), f"Driver source not found: {CODEX_SRC}"
    
    obj_o = os.path.join(TOOLS_DIR, "codex_mms.o")
    elf_out = os.path.join(TOOLS_DIR, "codex_mms.elf")
    ld_script = os.path.join(TOOLS_DIR, "mms_driver.ld")

    with open(ld_script, "w") as f:
        f.write("""SECTIONS {
    . = 0xfffffe00074e9220;
    .text : {
        *(.text*)
        *(.rodata*)
    }
    /DISCARD/ : {
        *(.data*)
        *(.bss*)
        *(COMMON)
        *(.eh_frame*)
        *(.note*)
        *(.comment*)
    }
}
""")

    print("[2/5] Compiling and linking with Zig...")
    subprocess.run([
        ZIG, "cc", "-target", "aarch64-freestanding-none", "-mcpu=cortex_a55",
        "-Os", "-ffreestanding", "-nostdlib", "-fno-pic", "-mstrict-align",
        "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
        "-c", CODEX_SRC, "-o", obj_o
    ], check=True)

    subprocess.run([
        ZIG, "cc", "-target", "aarch64-freestanding-none",
        "-nostdlib", "-static",
        f"-Wl,-T,{ld_script.replace('\\', '/')}", "-Wl,--build-id=none",
        obj_o, "-o", elf_out
    ], check=True)

    with open(elf_out, "rb") as f:
        elf_bytes = f.read()

    e_shoff, = struct.unpack_from("<Q", elf_bytes, 40)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", elf_bytes, 58)
    sh_str = elf_bytes[e_shoff + e_shstrndx * e_shentsize : e_shoff + (e_shstrndx + 1) * e_shentsize]
    str_off, str_sz = struct.unpack_from("<QQ", sh_str, 24)
    strtab = elf_bytes[str_off : str_off + str_sz]

    text_bytes = None
    text_addr = None
    for i in range(e_shnum):
        sh = elf_bytes[e_shoff + i * e_shentsize : e_shoff + (i + 1) * e_shentsize]
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size = struct.unpack("<IIQQQQ", sh[:40])
        name = strtab[sh_name : strtab.find(b"\0", sh_name)].decode()
        if name == ".text":
            text_bytes = elf_bytes[sh_offset : sh_offset + sh_size]
            text_addr = sh_addr
            break

    assert text_bytes is not None and text_addr == 0xfffffe00074e9220
    print(f"    Compiled .text payload: {len(text_bytes)} bytes at {text_addr:#x}")

    total_driver_size = 4 + len(text_bytes)
    assert total_driver_size <= 6008, f"Driver too large: {total_driver_size} > 6008"

    print("[3/5] Injecting payload into clean kernel baseline...")
    kernel_data = bytearray(open(KERNEL_GOOD, "rb").read())

    # Branch at 0xfffffe00074e921c (file offset 0x4e521c) to 0xfffffe00074e9220
    touch_entry_file_off = 0x004e521c
    struct.pack_into("<I", kernel_data, touch_entry_file_off, 0x14000001)
    kernel_data[touch_entry_file_off + 4 : touch_entry_file_off + 4 + len(text_bytes)] = text_bytes
    print(f"    Injected touch driver at file offset {touch_entry_file_off:#x} (size: {total_driver_size} bytes)")

    with open(KERNEL_TARGET, "wb") as f:
        f.write(kernel_data)
    print(f"    Saved patched kernel: {KERNEL_TARGET}")

    print("[4/5] Setting sweettouch=1 in build_sc54a_boot.py...")
    boot_script = os.path.join(TOOLS_DIR, "build_sc54a_boot.py")
    boot_code = open(boot_script, "r", encoding="utf-8").read()
    new_cmdline = (
        'CMDLINE = (\n'
        '    "debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress "\n'
        '    "rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin "\n'
        '    "ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 "\n'
        '    "sweetfb_noconsole=1 sweetfb_hz=30 sweetkeys=1 sweettouch=1 sweettouchdelay=5 sweetbattery=0 sweetusb=0"\n'
        ')'
    )
    boot_code = re.sub(r'CMDLINE = \([^)]+\)', new_cmdline, boot_code)
    open(boot_script, "w", encoding="utf-8").write(boot_code)

    print("[5/5] Building Odin AP boot.tar.md5...")
    subprocess.run([sys.executable, boot_script], check=True)

    # Also copy to Downloads folder for convenience
    for dl_path in [r"C:\Users\user\Downloads", r"C:\Users\user\Documents\Downloads"]:
        if os.path.isdir(dl_path):
            dst = os.path.join(dl_path, "boot.tar.md5")
            shutil.copyfile(r"C:\Users\user\Desktop\boot.tar.md5", dst)
            print(f"    Copied boot.tar.md5 to {dst}")

    print("\n>>> CODEX MELFAS MSS100 TOUCH INTEGRATION COMPLETED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    main()
