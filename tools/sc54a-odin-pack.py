#!/usr/bin/env python3
"""Pack Android partition images into Odin-flashable .tar / .tar.md5 archives.

Samsung Odin (and Heimdall) flash raw partition images (.img) packaged
inside a standard ustar tarball, optionally with an MD5 checksum footer.

Features:
  - Pack standalone boot.img -> boot.tar.md5 (for Odin AP slot)
  - Pack iOS rootfs as userdata.img -> userdata.tar.md5 (for Odin USERDATA slot)
  - Pack ALL-IN-ONE AP package containing both boot.img and userdata.img (single-shot flash without TWRP!)

Usage:
    python tools/sc54a-odin-pack.py [image_path] [output_dir]
    python tools/sc54a-odin-pack.py --boot boot.img --userdata ios16-root.img --out AP_ios_sc54a.tar.md5
"""
import argparse
import hashlib
import os
import sys
import tarfile

def make_tar_md5(file_dict, output_tar_path):
    """file_dict: {arcname: local_path}"""
    os.makedirs(os.path.dirname(os.path.abspath(output_tar_path)), exist_ok=True)
    base_name = os.path.basename(output_tar_path)
    if base_name.endswith('.md5'):
        tar_name = base_name[:-4]
    else:
        tar_name = base_name
        output_tar_path = output_tar_path + '.md5'

    temp_tar = os.path.join(os.path.dirname(os.path.abspath(output_tar_path)), tar_name)

    print(f"[*] Creating Odin archive {temp_tar}...")
    with tarfile.open(temp_tar, 'w', format=tarfile.USTAR_FORMAT) as tar:
        for arcname, local_path in file_dict.items():
            if not os.path.isfile(local_path):
                print(f"Error: file '{local_path}' not found.", file=sys.stderr)
                return False
            print(f"    + Adding {local_path} as {arcname} ({os.path.getsize(local_path):,} bytes)")
            tar.add(local_path, arcname=arcname)

    print("[*] Calculating MD5 checksum...")
    md5 = hashlib.md5()
    with open(temp_tar, 'rb') as f:
        while chunk := f.read(1024 * 1024):
            md5.update(chunk)
    md5_hash = md5.hexdigest()
    print(f"[*] MD5: {md5_hash}")

    # Create final .tar.md5 by appending the MD5 verification line
    with open(output_tar_path, 'wb') as f_out:
        with open(temp_tar, 'rb') as f_in:
            while chunk := f_in.read(1024 * 1024):
                f_out.write(chunk)
        f_out.write(f"{md5_hash}  {tar_name}\n".encode('ascii'))

    print(f"[+] Output ready: {output_tar_path} ({os.path.getsize(output_tar_path):,} bytes)")
    return True

def pack_single(image_path, output_dir=None):
    if not os.path.isfile(image_path):
        print(f"Error: image '{image_path}' not found.", file=sys.stderr)
        return False

    base = os.path.basename(image_path).lower()
    if 'boot' in base:
        arcname = 'boot.img'
        tar_name = 'boot.tar.md5'
    elif 'userdata' in base or 'root' in base:
        arcname = 'userdata.img'
        tar_name = 'userdata.tar.md5'
    elif 'recovery' in base:
        arcname = 'recovery.img'
        tar_name = 'recovery.tar.md5'
    else:
        arcname = os.path.basename(image_path)
        tar_name = os.path.splitext(arcname)[0] + '.tar.md5'

    out_dir = output_dir or os.path.dirname(image_path) or '.'
    return make_tar_md5({arcname: image_path}, os.path.join(out_dir, tar_name))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Package partition images for Odin flashing")
    parser.add_argument('input', nargs='?', default=None, help="Path to single input image (boot.img or userdata.img)")
    parser.add_argument('output_dir', nargs='?', default=None, help="Output directory")
    parser.add_argument('--boot', help="Path to boot.img")
    parser.add_argument('--userdata', help="Path to userdata/rootfs image")
    parser.add_argument('--recovery', help="Path to recovery.img")
    parser.add_argument('--out', help="Path to output .tar or .tar.md5 archive")
    args = parser.parse_args()

    if args.boot or args.userdata or args.recovery:
        mapping = {}
        if args.boot:
            mapping['boot.img'] = args.boot
        if args.userdata:
            mapping['userdata.img'] = args.userdata
        if args.recovery:
            mapping['recovery.img'] = args.recovery
        out_path = args.out or 'shim/out/AP_ios_sc54a.tar.md5'
        if not make_tar_md5(mapping, out_path):
            sys.exit(1)
    elif args.input:
        if not pack_single(args.input, args.output_dir):
            sys.exit(1)
    else:
        # Default behavior: try packaging shim/out/boot.img
        default_boot = 'shim/out/boot.img'
        if os.path.isfile(default_boot):
            pack_single(default_boot, 'shim/out')
        else:
            parser.print_help()
