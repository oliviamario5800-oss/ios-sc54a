import struct

with open(r'C:\Users\user\Projects\ios-sc54a\stock\dtbo.img', 'rb') as f:
    dtbo = f.read()

dtb_start = dtbo.rfind(b'\xd0\x0d\xfe\xed', 0, 0x3e872)
magic, totalsize, off_dt_struct, off_dt_strings = struct.unpack('>4I', dtbo[dtb_start:dtb_start+16])
print('dtb_start:', hex(dtb_start), 'totalsize:', totalsize)

pos = 0x3e872 - dtb_start
p = pos - 4
depth = 0

while p < totalsize:
    tag, = struct.unpack('>I', dtbo[dtb_start+p:dtb_start+p+4])
    p += 4
    if tag == 1:
        name_end = dtbo.find(b'\x00', dtb_start+p) - dtb_start
        nodename = dtbo[dtb_start+p : dtb_start+name_end].decode('latin1')
        p = (name_end + 4) & ~3
        print('  ' * depth + f'Node: {nodename}')
        depth += 1
    elif tag == 2:
        depth -= 1
        if depth == 0:
            break
    elif tag == 3:
        length, nameoff = struct.unpack('>II', dtbo[dtb_start+p:dtb_start+p+8])
        p += 8
        propname = dtbo[dtb_start+off_dt_strings + nameoff : dtbo.find(b'\x00', dtb_start+off_dt_strings + nameoff)].decode('latin1')
        val = dtbo[dtb_start+p : dtb_start+p+length]
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
