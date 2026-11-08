with open(r'C:\Users\user\Projects\ios-sc54a\stock\stock_linux_kernel', 'rb') as f:
    data = f.read()

import struct

# p = 0x24ba988
# Let's inspect the pingroups table around 0x24ba900
# Let's check size of msm_pingroup
# In pingroup 6, name pointer is at 0x24ba988 (VA 0xffffff800a014128) -> wait, 0x1f94128 + 0x8080000 = 0xa014128!
# Let's check:
for g in range(5, 9):
    # let's search where name pointer to gpio{g} is
    vag = 0xffffff8008080000 + data.find(f'gpio{g}\0'.encode())
    off = data.find(struct.pack('<Q', vag))
    print(f'gpio{g} pingroup entry at {hex(off)}')
    # Let's dump 64 bytes
    entry = data[off : off + 72]
    # In Linux ARM64:
    # const char *name (8)
    # const unsigned *pins (8)
    # unsigned npins (4)
    # unsigned funcs[8]?
    print('  entry bytes:', entry[:40].hex())
