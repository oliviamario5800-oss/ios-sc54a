import struct

with open(r'C:\Users\user\Projects\ios-sc54a\stock\dtbo.img', 'rb') as f:
    data = f.read()

dt1 = data[0x1a6 : 0x1a6 + 368288]
magic, totalsize, off_dt_struct, off_dt_strings = struct.unpack('>4I', dt1[:16])

# Find 0x000000cc
for ph in [0xcc, 0xcd]:
    pos = 0
    pattern = b'\x00\x00\x00\x03\x00\x00\x00\x04' # FDT_PROP, len 4
    while True:
        idx = dt1.find(struct.pack('>I', ph), pos)
        if idx == -1:
            break
        # check if preceded by phandle prop
        p_prop = dt1.rfind(b'\x00\x00\x00\x03', 0, idx)
        if p_prop != -1 and idx - p_prop < 16:
            length, nameoff = struct.unpack('>II', dt1[p_prop+4:p_prop+12])
            propname = dt1[off_dt_strings + nameoff : dt1.find(b'\x00', off_dt_strings + nameoff)].decode('latin1')
            if 'phandle' in propname:
                # find enclosing node
                p_node = dt1.rfind(b'\x00\x00\x00\x01', 0, p_prop)
                nodename = dt1[p_node+4 : dt1.find(b'\x00', p_node+4)].decode('latin1')
                print(f'phandle {hex(ph)} in node: {nodename}')
                # dump all props of this node
                p = p_node + 4
                name_end = dt1.find(b'\x00', p)
                p = (name_end + 4) & ~3
                while p < len(dt1):
                    tag, = struct.unpack('>I', dt1[p:p+4])
                    p += 4
                    if tag == 2: break
                    if tag == 3:
                        l, noff = struct.unpack('>II', dt1[p:p+8])
                        p += 8
                        pname = dt1[off_dt_strings + noff : dt1.find(b'\x00', off_dt_strings + noff)].decode('latin1')
                        val = dt1[p:p+l]
                        p = (p + l + 3) & ~3
                        print(f'    {pname} = {val.hex()}')
        pos = idx + 4
