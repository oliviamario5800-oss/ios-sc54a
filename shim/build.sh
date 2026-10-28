#!/bin/bash
# Build the shim for Samsung Galaxy A51 5G (SC-54A / a51xq, SM7250)
# Outputs:
#   out/Image          - Bare shim binary
#   out/shim-boot.img  - Boot image v2
#   out/boot.img       - Samsung-compatible boot partition image
#   out/boot.tar       - Odin AP slot flashable archive
#   out/boot.tar.md5   - Odin AP slot verified tar with md5 footer
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

# The image runs wherever bootloader puts it with no relocation pass: only the
# header's image_size (start.o, linked at 0) may be an absolute address.
abs=$($READELF -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }

$CC $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o -o out/shim.elf

$OBJCOPY -O binary out/shim.elf out/Image

# Bootloader copies the file; pad to image_size so .bss/stacks exist as zeroed bytes.
size=$($NM out/shim.elf | awk '$3=="_end"{print strtonum("0x"$1)}')
pad=$(( (size + 4095) & ~4095 ))
truncate -s "$pad" out/Image

# Ramdisk & payload embedding:
if [ -n "${1:-}" ]; then
	cp "$1" out/ramdisk.bin
	# Embed payload after _end in Image as well for Samsung bootloaders that omit initrd
	cat "$1" >> out/Image
else
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi

DTB_ARG=""
if [ -f ../dtb/boot_unpacked/dtb ]; then
	DTB_ARG="--dtb ../dtb/boot_unpacked/dtb --dtb_offset 0x1f00000"
fi

if command -v mkbootimg >/dev/null 2>&1; then
	mkbootimg --header_version 2 --kernel out/Image --ramdisk out/ramdisk.bin \
		$DTB_ARG --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \
		--tags_offset 0x100 --pagesize 4096 \
		--os_version 17.0.0 --os_patch_level 2035-06 \
		--cmdline "console=null" -o out/shim-boot.img
	cp out/shim-boot.img out/boot.img

	# Package for Odin flashing (Samsung Download Mode)
	if command -v tar >/dev/null 2>&1; then
		(cd out && tar -H ustar -cf boot.tar boot.img)
		if command -v md5sum >/dev/null 2>&1; then
			(cd out && cp boot.tar boot.tar.md5 && md5sum -t boot.img >> boot.tar.md5)
		fi
	fi
fi

# Host render of the console layout
if command -v gcc >/dev/null 2>&1; then
	gcc -O1 -Wall -o out/host_test console.c host_test.c && ./out/host_test || true
fi

echo "SC-54A shim build complete."
ls -l out/Image
