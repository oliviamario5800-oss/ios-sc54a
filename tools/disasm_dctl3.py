import capstone

with open(r'C:\Users\user\Projects\ios-sc54a\xnu\kernel.development.sc54a.bak', 'rb') as f:
    data = f.read()

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

file_off = 0x4e9640 # 0xfffffe00074ed640
code = data[file_off : file_off + 0x60]
for insn in md.disasm(code, 0xfffffe00074ed640):
    print(f'{hex(insn.address)}: {insn.mnemonic} {insn.op_str}')
