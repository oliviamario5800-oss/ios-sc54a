#!/usr/bin/env python3
"""Pack an Android boot.img into an Odin-flashable boot.tar / boot.tar.md5

Samsung Odin (and Heimdall) expect boot partition images packaged inside
a standard ustar tarball, optionally terminated by an MD5 checksum footer.

Usage:
    python tools/sc54a-odin-pack.py [boot.img] [output_dir]
"""
import argparse
import hashlib
import os
import sys
import tarfile

def pack_odin_tar(boot_img_path, output_dir=None):
    if not os.path.isfile(boot_img_path):
        print(f"Error: boot image '{boot_img_path}' not found.", file=sys.stderr)
        return False

    if output_dir is None:
        output_dir = os.path.dirname(boot_img_path) or '.'
    os.makedirs(output_dir, exist_ok=True)

    tar_path = os.path.join(output_dir, 'boot.tar')
    tar_md5_path = os.path.join(output_dir, 'boot.tar.md5')

    print(f"[*] Packaging {boot_img_path} into {tar_path}...")
    with tarfile.open(tar_path, 'w', format=tarfile.USTAR_FORMAT) as tar:
        tar.add(boot_img_path, arcname='boot.img')

    # Read tar data and calculate MD5
    with open(tar_path, 'rb') as f:
        tar_bytes = f.read()

    md5_hash = hashlib.md5(tar_bytes).hexdigest()
    print(f"[*] MD5 checksum: {md5_hash}")

    # Samsung Odin .tar.md5 is simply the tarball plus md5 checksum line
    with open(tar_md5_path, 'wb') as f:
        f.write(tar_bytes)
        f.write(f"{md5_hash}  boot.tar\n".encode('ascii'))

    print(f"[+] Successfully generated Odin flashable archives:")
    print(f"    - {tar_path} ({os.path.getsize(tar_path)} bytes)")
    print(f"    - {tar_md5_path} ({os.path.getsize(tar_md5_path)} bytes)")
    print(f"[*] Load boot.tar.md5 into the 'AP' slot in Odin while device is in Download Mode.")
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Package boot.img into Odin flashable boot.tar / boot.tar.md5")
    parser.add_argument('boot_img', nargs='?', default='shim/out/boot.img', help="Path to input boot.img")
    parser.add_argument('output_dir', nargs='?', default=None, help="Output directory for tar archive")
    args = parser.parse_args()

    if not pack_odin_tar(args.boot_img, args.output_dir):
        sys.exit(1)
