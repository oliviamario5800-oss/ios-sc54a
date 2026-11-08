with open(r'C:\Users\user\Projects\ios-sc54a\stock\stock_linux_kernel', 'rb') as f:
    data = f.read()

import struct

# group 6
for g in [6, 7, 9]:
    off = 0x1a10980 + g * 0x58
    chunk = data[off : off + 0x58]
    print(f'=== GROUP {g} at {hex(off)} ===')
    # Let's inspect fields
    # name (8), pins (8), npins (4), funcs (array of ints? or int funcs[8]?)
    for i in range(0, len(chunk), 4):
        v, = struct.unpack('<I', chunk[i:i+4])
        print(f'  +{hex(i)}: {hex(v)} ({v})')
