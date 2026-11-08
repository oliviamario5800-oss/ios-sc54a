#!/bin/bash
# Build the shim for Samsung Galaxy A51 5G (SC-54A / a51xq, SM7250)
set -euo pipefail
cd "$(dirname "$0")"

CC=${CROSS_COMPILE:-aarch64-linux-gnu-}gcc
OBJCOPY=${CROSS_COMPILE:-aarch64-linux-gnu-}objcopy
NM=${CROSS_COMPILE:-aarch64-linux-gnu-}nm
READELF=${CROSS_COMPILE:-aarch64-linux-gnu-}readelf

CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"
mkdir -p out

echo "Compiling SC-54A shim objects..."
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c; do
	$CC $CFLAGS -c $f -o out/${f%.*}.o
done

abs=$($READELF -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }

$CC $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o -o out/shim.elf

$OBJCOPY -O binary out/shim.elf out/Image

size=$($NM out/shim.elf | awk '$3=="_end"{print strtonum("0x"$1)}')
pad=$(( (size + 4095) & ~4095 ))
truncate -s "$pad" out/Image

# Ramdisk & payload embedding:
if [ -n "${1:-}" ]; then
	cp "$1" out/ramdisk.bin
	cat "$1" >> out/Image
else
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi

echo "Repacking boot.img via magiskboot with stock SC-54A DTB and headers..."
cp out/Image ../stock/unpacked/kernel
(cd ../stock/unpacked && PATCHVBMETAFLAG=true ../magiskboot repack ../boot.img /mnt/c/Users/Owner/Projects/ios-sc54a/shim/out/magisk-boot.img)

echo "SC-54A shim build complete."
ls -lh out/Image out/magisk-boot.img
