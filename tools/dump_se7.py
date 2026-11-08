import struct

with open(r'C:\Users\user\Projects\ios-sc54a\stock\unpacked\dtb', 'rb') as f:
    dt = f.read(402116)

magic, totalsize, off_dt_struct, off_dt_strings = struct.unpack('>4I', dt[:16])

# Find qupv3_se7_i2c or 984000
pos = dt.find(b'i2c@984000\x00')
if pos == -1:
    pos = dt.find(b'qupv3_se7_i2c\x00')
print('Node pos:', hex(pos))

p = pos - 4
depth = 0
while p < len(dt):
    tag, = struct.unpack('>I', dt[p:p+4])
    p += 4
    if tag == 1:
        name_end = dt.find(b'\x00', p)
        nodename = dt[p:name_end].decode('latin1')
        p = (name_end + 4) & ~3
        print('  ' * depth + f'Node: {nodename}')
        depth += 1
    elif tag == 2:
        depth -= 1
        print('  ' * depth + 'End Node')
        if depth == 0:
            break
    elif tag == 3:
        length, nameoff = struct.unpack('>II', dt[p:p+8])
        p += 8
        propname = dt[off_dt_strings + nameoff : dt.find(b'\x00', off_dt_strings + nameoff)].decode('latin1')
        val = dt[p:p+length]
        p = (p + length + 3) & ~3
        val_str = None
        if length > 0 and val[-1] == 0:
            try:
                s = val[:-1].decode('ascii')
                if all(32 <= ord(c) < 127 or c in '\n\r\t' for c in s):
                    val_str = f'string: "{s}"'
            except:
                pass
        if val_str is None:
            if length == 4:
                val_str = f'u32: {hex(struct.unpack(">I", val)[0])}'
            elif length == 8:
                val_str = f'u64: {hex(struct.unpack(">Q", val)[0])}'
            else:
                val_str = f'bytes({length}): {val.hex()}'
        print('  ' * depth + f'Prop: {propname} = {val_str}')
    elif tag == 4:
        continue
    elif tag == 9:
        break
