with open(r'C:\Users\user\Projects\ios-sc54a\stock\stock_linux_kernel', 'rb') as f:
    data = f.read()

import struct

p = 0x24bb438
for i in range(25):
    r_off, r_info, r_addend = struct.unpack('<QQQ', data[p + i*24 : p + (i+1)*24])
    s = ''
    str_off = r_addend - 0xffffff8008080000
    if 0 <= str_off < len(data):
        s = data[str_off : data.find(b'\0', str_off)].decode('latin1', errors='replace')
    print(f'{i}: r_off={hex(r_off)} s="{s}"')
