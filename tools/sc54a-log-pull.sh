#!/bin/sh
# Read the SC-54A console log from Samsung Galaxy A51 5G booted into TWRP recovery.
# Usage:
#   tools/sc54a-log-pull.sh [-s SERIAL] OUT.txt
# Reads: the last 1025 blocks (4 KiB) of the userdata partition.
set -eu

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
	echo "Usage: $(basename "$0") [-s SERIAL] OUT.txt"
	echo "Pull the 4 MiB kernel log ring from userdata via TWRP recovery ADB."
	exit 0
fi

ADB="adb"
if [ "${1:-}" = "-s" ]; then
	shift
	SERIAL=${1:?Error: -s requires a device serial}
	shift
	ADB="adb -s $SERIAL"
fi

out=${1:?usage: sc54a-log-pull.sh [-s SERIAL] OUT.txt}
command -v adb >/dev/null 2>&1 || { echo "Error: adb not found in PATH" >&2; exit 1; }

dev=$($ADB shell 'for d in /dev/block/by-name/userdata /dev/block/bootdevice/by-name/userdata /dev/block/platform/soc/1d84000.ufshc/by-name/userdata; do [ -e $d ] && readlink -f $d && break; done' 2>/dev/null | tr -d '\r')
[ -n "$dev" ] || { echo "userdata block device not found on connected device" >&2; exit 1; }
bytes=$($ADB shell blockdev --getsize64 "$dev" | tr -d '\r')
skip=$((bytes / 4096 - 1025))
$ADB exec-out "dd if=$dev bs=4096 skip=$skip count=1025 2>/dev/null" > "$out.bin"
echo "$dev: $bytes bytes, read blocks $skip.. ($(stat -c %s "$out.bin") bytes)" >&2
python3 "$(dirname "$0")/sweet-log.py" "$out.bin" "$out"
