import capstone

with open(r'C:\Users\user\Projects\ios-sc54a\xnu\kernel.development.sc54a.bak', 'rb') as f:
    data = f.read()

md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)

file_off = 0x4e8f40 # 0xfffffe00074ecf40
code = data[file_off : file_off + 0x100]
for insn in md.disasm(code, 0xfffffe00074ecf40):
    print(f'{hex(insn.address)}: {insn.mnemonic} {insn.op_str}')
