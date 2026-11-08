import capstone

with open(r'C:\Users\user\Projects\ios-sc54a\xnu\kernel.development.sc54a.bak', 'rb') as f:
    data = f.read()

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

# File offset = VA - 0xfffffe0007004000
# VA 0xfffffe00074ec980 -> file offset 0x4e8980
file_off = 0x4e8980
code = data[file_off : file_off + 0x300]
for insn in md.disasm(code, 0xfffffe00074ec980):
    print(f'{hex(insn.address)}: {insn.mnemonic} {insn.op_str}')
