import subprocess, os, struct, capstone

ZIG = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"

ld_script = """
SECTIONS {
    . = 0xfffffe00074e921c;
    .text : {
        *(.entry)
        *(.text*)
        *(.rodata*)
        *(.data*)
        *(.bss*)
        *(COMMON)
    }
    /DISCARD/ : {
        *(.eh_frame*)
        *(.note*)
        *(.comment*)
    }
}
"""
open(r"C:\Users\user\Projects\ios-sc54a\tools\test.ld", "w").write(ld_script)

test_s = """
.section .entry, "ax", @progbits
.balign 4
.global touch_entry
touch_entry:
    b real_thread
"""
open(r"C:\Users\user\Projects\ios-sc54a\tools\entry.S", "w").write(test_s)

test_c = """
#include <stdint.h>
void real_thread(void) {
    volatile int x = 42;
}
"""
open(r"C:\Users\user\Projects\ios-sc54a\tools\test_entry.c", "w").write(test_c)

subprocess.run([
    ZIG, "cc", "-target", "aarch64-freestanding-none", "-mcpu=cortex_a55",
    "-c", r"C:\Users\user\Projects\ios-sc54a\tools\entry.S",
    "-o", r"C:\Users\user\Projects\ios-sc54a\tools\entry.o"
], check=True)

subprocess.run([
    ZIG, "cc", "-target", "aarch64-freestanding-none", "-mcpu=cortex_a55",
    "-O2", "-ffreestanding", "-nostdlib", "-fno-pic", "-mstrict-align",
    "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
    "-c", r"C:\Users\user\Projects\ios-sc54a\tools\test_entry.c",
    "-o", r"C:\Users\user\Projects\ios-sc54a\tools\test_entry.o"
], check=True)

subprocess.run([
    ZIG, "cc", "-target", "aarch64-freestanding-none",
    "-nostdlib", "-static",
    "-Wl,-T,C:/Users/user/Projects/ios-sc54a/tools/test.ld", "-Wl,--build-id=none",
    r"C:\Users\user\Projects\ios-sc54a\tools\entry.o",
    r"C:\Users\user\Projects\ios-sc54a\tools\test_entry.o",
    "-o", r"C:\Users\user\Projects\ios-sc54a\tools\test_entry.elf"
], check=True)

b = open(r"C:\Users\user\Projects\ios-sc54a\tools\test_entry.elf", "rb").read()
e_shoff, = struct.unpack_from("<Q", b, 40)
e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", b, 58)

sh_str = b[e_shoff + e_shstrndx * e_shentsize : e_shoff + (e_shstrndx + 1) * e_shentsize]
str_off, str_sz = struct.unpack_from("<QQ", sh_str, 24)
strtab = b[str_off : str_off + str_sz]

for i in range(e_shnum):
    sh = b[e_shoff + i * e_shentsize : e_shoff + (i + 1) * e_shentsize]
    sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_addralign, sh_entsize = struct.unpack("<IIQQQQIIQQ", sh)
    name = strtab[sh_name : strtab.find(b"\0", sh_name)].decode()
    if sh_size > 0:
        print(f"{name:20s}: addr=0x{sh_addr:x}, off=0x{sh_offset:x}, sz=0x{sh_size:x}, align={sh_addralign}")

# Disassemble
md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
for i in range(e_shnum):
    sh = b[e_shoff + i * e_shentsize : e_shoff + (i + 1) * e_shentsize]
    sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size = struct.unpack("<IIQQQQ", sh[:36])
    name = strtab[sh_name : strtab.find(b"\0", sh_name)].decode()
    if name == ".text":
        for insn in md.disasm(b[sh_offset : sh_offset + sh_size], sh_addr):
            print(f"  0x{insn.address:x}: {insn.mnemonic:8s} {insn.op_str}")
