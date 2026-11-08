with open(r'C:\Users\user\Projects\ios-sc54a\stock\stock_linux_kernel', 'rb') as f:
    data = f.read()

import struct

# Let's search for 0x18158
needle = struct.pack('<I', 0x18158)
pos = 0
while True:
    idx = data.find(needle, pos)
    if idx == -1: break
    print('Found 0x18158 at', hex(idx))
    # print context
    print(data[idx-16:idx+32].hex())
    pos = idx + 4
