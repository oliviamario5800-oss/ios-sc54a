import struct

with open(r'C:\Users\user\Projects\ios-sc54a\stock\dtbo.img', 'rb') as f:
    data = f.read()

dt1 = data[0x1a6 : 0x1a6 + 368288]
magic, totalsize, off_dt_struct, off_dt_strings = struct.unpack('>4I', dt1[:16])

mms_off = dt1.find(b'mms_ts@48\x00')
print('mms_off:', hex(mms_off))

p = mms_off - 4
depth = 0
while p < len(dt1):
    tag, = struct.unpack('>I', dt1[p:p+4])
    p += 4
    if tag == 1:
        name_end = dt1.find(b'\x00', p)
        nodename = dt1[p:name_end].decode('latin1')
        p = (name_end + 4) & ~3
        print('  ' * depth + f'Node: {nodename}')
        depth += 1
    elif tag == 2:
        depth -= 1
        print('  ' * depth + 'End Node')
        if depth == 0:
            break
    elif tag == 3:
        length, nameoff = struct.unpack('>II', dt1[p:p+8])
        p += 8
        propname = dt1[off_dt_strings + nameoff : dt1.find(b'\x00', off_dt_strings + nameoff)].decode('latin1')
        val = dt1[p:p+length]
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
            else:
                val_str = f'bytes({length}): {val.hex()}'
        print('  ' * depth + f'Prop: {propname} = {val_str}')
    elif tag == 4:
        continue
    elif tag == 9:
        break
