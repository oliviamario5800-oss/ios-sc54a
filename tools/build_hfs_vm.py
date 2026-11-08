#!/usr/bin/env python3
"""Prepare tweaks.tar (applying mkiosroot.sh + sc54a-root-tweaks.py logic) and
assemble the self-contained Alpine Linux x86_64 initramfs for building the 16 GiB
case-sensitive journaled HFS+ iOS 16.7.16 userdata image inside QEMU.
"""
import fnmatch, gzip, io, os, plistlib, shutil, subprocess, sys, tarfile

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TOOLS_DIR)
IPSW_DIR = os.path.join(PROJ_DIR, "research", "ipsw-x")
ALP_DIR = os.path.join(PROJ_DIR, "research", "alpine-hfs")

BUILD, VERSION = "20H392", "16.7.16"
MOBILE = 501


def extract_matching_from_tar(tar_path, patterns):
    """Fast streaming scan of an uncompressed tar, returning {name: (TarInfo, bytes)}."""
    found = {}
    with tarfile.open(tar_path, "r:") as tf:
        for m in tf:
            if not m.isfile():
                continue
            name = m.name.lstrip("./")
            for pat in patterns:
                if fnmatch.fnmatch(name, pat):
                    found[name] = (m, tf.extractfile(m).read())
                    break
    return found


def add_bytes(tf, name, data, uid, gid, mode, mtime=0):
    ti = tarfile.TarInfo(name=name)
    ti.type = tarfile.REGTYPE
    ti.size = len(data)
    ti.uid = uid
    ti.gid = gid
    ti.mode = mode
    ti.mtime = mtime
    tf.addfile(ti, io.BytesIO(data))


def add_dir(tf, name, uid, gid, mode, mtime=0):
    ti = tarfile.TarInfo(name=name.rstrip("/") + "/")
    ti.type = tarfile.DIRTYPE
    ti.size = 0
    ti.uid = uid
    ti.gid = gid
    ti.mode = mode
    ti.mtime = mtime
    tf.addfile(ti)


def build_tweaks_tar(os_tar_path, sysos_tar_path, out_tweaks_tar):
    patterns = [
        "System/Library/xpc/launchd.plist",
        "System/Library/Frameworks/MediaToolbox.framework/*/SubServerConfiguration.plist",
        "private/var/containers/Data/System/*/.com.apple.mobile_container_manager.metadata.plist",
        "private/var/mobile/Library/Preferences/com.apple.migration.plist",
        "private/var/mobile/Library/Preferences/com.apple.springboard.plist",
        "private/var/mobile/Library/Preferences/com.apple.purplebuddy.plist",
        "private/var/mobile/Library/Preferences/com.apple.purplebuddy.notbackedup.plist",
        "private/var/mobile/Library/Preferences/com.apple.managedconfiguration.notbackedup.plist",
    ]
    print(f"[tweaks] Scanning {os_tar_path} for plist targets...")
    matched = extract_matching_from_tar(os_tar_path, patterns)
    if "System/Library/xpc/launchd.plist" not in matched:
        print(f"[tweaks] Also checking {sysos_tar_path} for launchd.plist...")
        matched.update(extract_matching_from_tar(sysos_tar_path, ["private/preboot/Cryptexes/OS/System/Library/xpc/launchd.plist"]))

    with tarfile.open(out_tweaks_tar, "w:", format=tarfile.GNU_FORMAT) as out:
        # 1. launchd.plist (_PanicOnCrash -> _PanicOnCrasX, remove com.apple.CommCenter)
        ld_key = "System/Library/xpc/launchd.plist"
        assert ld_key in matched, "System/Library/xpc/launchd.plist not found in os.tar!"
        ld_info, ld_raw = matched[ld_key]
        plist = plistlib.loads(ld_raw)
        jobs = plist["LaunchDaemons"]
        renamed = 0
        for job in jobs.values():
            if "_PanicOnCrash" in job:
                job["_PanicOnCrasX"] = job.pop("_PanicOnCrash")
                renamed += 1
        removed = [k for k, j in jobs.items() if j.get("Label") == "com.apple.CommCenter"]
        for k in removed:
            del jobs[k]
        ld_out = plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)
        add_bytes(out, ld_key, ld_out, ld_info.uid, ld_info.gid, ld_info.mode & 0o7777, ld_info.mtime)
        print(f"[tweaks] launchd.plist: {renamed} _PanicOnCrash renamed, {len(removed)} CommCenter jobs removed")

        # 2. MediaToolbox SubServerConfiguration.plist (StartRecorderServer = False)
        mt_count = 0
        for k, (info, raw) in sorted(matched.items()):
            if k.startswith("System/Library/Frameworks/MediaToolbox.framework/") and k.endswith("/SubServerConfiguration.plist"):
                data = plistlib.loads(raw)
                data["StartRecorderServer"] = False
                xml_out = plistlib.dumps(data, fmt=plistlib.FMT_XML)
                add_bytes(out, k, xml_out, info.uid, info.gid, info.mode & 0o7777, info.mtime)
                mt_count += 1
                print(f"[tweaks] StartRecorderServer=False in {k}")
        assert mt_count > 0, "No MediaToolbox SubServerConfiguration.plist found!"

        # 3. /private/var/containers/{Bundle,Data/System,Shared/SystemGroup,Temp}
        add_dir(out, "private/var/containers", 0, 0, 0o755)
        add_dir(out, "private/var/containers/Bundle", 33, 33, 0o755)
        add_dir(out, "private/var/containers/Data", 0, 0, 0o755)
        add_dir(out, "private/var/containers/Data/System", 0, 0, 0o755)
        add_dir(out, "private/var/containers/Shared", 0, 0, 0o755)
        add_dir(out, "private/var/containers/Shared/SystemGroup", 0, 0, 0o755)
        add_dir(out, "private/var/containers/Temp", 0, 0, 0o755)

        # 4. mobileactivationd data_ark.plist if container exists
        for k, (_, raw) in matched.items():
            if k.endswith("/.com.apple.mobile_container_manager.metadata.plist"):
                if plistlib.loads(raw).get("MCMMetadataIdentifier") == "com.apple.mobileactivationd":
                    cdir = os.path.dirname(k)
                    idir = f"{cdir}/Library/internal"
                    add_dir(out, f"{cdir}/Library", MOBILE, MOBILE, 0o755)
                    add_dir(out, idir, MOBILE, MOBILE, 0o755)
                    ark = plistlib.dumps({
                        "-ActivationState": "Activated",
                        "-BrickState": False,
                        "-BuildVersion": BUILD,
                        "-ProductVersion": VERSION,
                        "-DataMigrationCompleted": True,
                        "-PreSydroDataMigrationCompleted": True,
                    }, fmt=plistlib.FMT_BINARY)
                    add_bytes(out, f"{idir}/data_ark.plist", ark, MOBILE, MOBILE, 0o600)
                    print(f"[tweaks] Seeded data_ark.plist in {idir}")

        # 5. Mobile Preferences (exact match with sc54a-root-tweaks.py)
        prefs = {
            "com.apple.migration": {
                "LastSystemVersion": BUILD,
                "DMUserDataDisposition": {"SyntheticDidUpgrade": False, "buildVersion": BUILD},
            },
            "com.apple.springboard": {
                "BKNoWatchdogs": True,
                "FBLaunchWatchdogExceptions": {"com.apple.purplebuddy": 100.0},
                "MCForceShowCloudConfigurationUI": False,
            },
            "com.apple.purplebuddy": {
                "SetupDone": True,
                "SetupFinishedAllSteps": True,
                "SetupVersion": 11,
                "daemonizeNeedsToRun": False,
            },
            "com.apple.purplebuddy.notbackedup": {
                k: False for k in (
                    "ShouldRunDiagnosticsMiniBuddy",
                    "ShouldRunHSA2MiniBuddy",
                    "ShouldRunKeychainSyncMiniBuddy",
                    "ShouldRunMesaMiniBuddy",
                    "ShouldRunPaymentMiniBuddy",
                    "ShouldRunStandardMiniBuddy",
                    "SilentICDPUpgrade",
                )
            },
            "com.apple.managedconfiguration.notbackedup": {
                "LockdownActivationIndicatesCloudConfigurationAvailable": False,
            },
        }
        for name, values in prefs.items():
            rel = f"private/var/mobile/Library/Preferences/{name}.plist"
            base = plistlib.loads(matched[rel][1]) if rel in matched else {}
            base.update(values)
            add_bytes(out, rel, plistlib.dumps(base, fmt=plistlib.FMT_BINARY), MOBILE, MOBILE, 0o600)
            print(f"[tweaks] pref {name}: {sorted(values)}")

        # 6. logd directories & .migrated markers (mkiosroot.sh lines 116-121)
        add_dir(out, "private/var/db/diagnostics", 272, 272, 0o751)
        add_dir(out, "private/var/db/uuidtext", 272, 272, 0o755)
        add_dir(out, "private/var/db/diagnostics/timesync", 272, 272, 0o755)
        for d in ("diagnostics", "uuidtext", "diagnostics/timesync"):
            add_bytes(out, f"private/var/db/{d}/.migrated", b"", 272, 272, 0o644)

        # 7. System keybag (mkiosroot.sh lines 124-128)
        add_dir(out, "private/var/keybags", 0, 0, 0o700)
        kb = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            b'<plist version="1.0"><dict><key>KeyBagKeys</key><data>U1dFRVQ=</data></dict></plist>\n'
        )
        add_bytes(out, "private/var/keybags/systembag.kb", kb, 0, 0, 0o600)
        print(f"[tweaks] Wrote {out_tweaks_tar}")


class CpioNewcWriter:
    def __init__(self):
        self.buf = io.BytesIO()
        self.ino = 300000

    def _align4(self, n):
        rem = n & 3
        if rem:
            self.buf.write(b"\x00" * (4 - rem))

    def add(self, name, mode, data=b"", uid=0, gid=0, nlink=1):
        name_b = name.lstrip("/").encode("utf-8") + b"\x00"
        self.ino += 1
        hdr = (
            "070701"
            f"{self.ino:08x}"
            f"{mode:08x}"
            f"{uid:08x}"
            f"{gid:08x}"
            f"{nlink:08x}"
            f"{0:08x}"
            f"{len(data):08x}"
            f"{0:08x}{0:08x}{0:08x}{0:08x}"
            f"{len(name_b):08x}"
            f"{0:08x}"
        ).encode("ascii")
        assert len(hdr) == 110
        self.buf.write(hdr)
        self.buf.write(name_b)
        self._align4(110 + len(name_b))
        if data:
            self.buf.write(data)
            self._align4(len(data))

    def finish_gzip(self):
        self.add("TRAILER!!!", 0, b"", nlink=1)
        raw = self.buf.getvalue()
        rem = len(raw) % 512
        if rem:
            raw += b"\x00" * (512 - rem)
        return gzip.compress(raw, compresslevel=6)


def build_hfs_initramfs(out_initramfs):
    cpio = CpioNewcWriter()
    for d in ("modules", "usr", "usr/local", "usr/local/bin", "usr/sbin", "usr/lib", "mnt"):
        cpio.add(d, 0o040755, nlink=2)

    # 1. Extract needed kernel modules from linux-lts.apk (gunzip .ko.gz -> .ko)
    wanted_mods = {
        "virtio.ko.gz",
        "virtio_ring.ko.gz",
        "virtio_pci_legacy_dev.ko.gz",
        "virtio_pci_modern_dev.ko.gz",
        "virtio_pci.ko.gz",
        "virtio_blk.ko.gz",
        "cdrom.ko.gz",
        "nls_utf8.ko.gz",
        "hfsplus.ko.gz",
    }
    found_mods = set()
    with tarfile.open(os.path.join(ALP_DIR, "linux-lts.apk"), "r:gz") as tf:
        for m in tf.getmembers():
            base = os.path.basename(m.name)
            if base in wanted_mods:
                ko_bytes = gzip.decompress(tf.extractfile(m).read())
                ko_name = base[:-3]  # strip .gz
                cpio.add(f"modules/{ko_name}", 0o100644, ko_bytes)
                found_mods.add(base)
    assert found_mods == wanted_mods, f"Missing modules: {wanted_mods - found_mods}"

    # 2. Extract hfsprogs, GNU tar, libacl, libcrypto3
    with tarfile.open(os.path.join(ALP_DIR, "hfsprogs.apk"), "r:gz") as tf:
        fsck_bytes = tf.extractfile("usr/sbin/fsck.hfs").read()
        mkfs_bytes = tf.extractfile("usr/sbin/mkfs.hfsplus").read()
        cpio.add("usr/sbin/fsck.hfs", 0o100755, fsck_bytes)
        cpio.add("usr/sbin/fsck.hfsplus", 0o120777, b"fsck.hfs")
        cpio.add("usr/sbin/mkfs.hfsplus", 0o100755, mkfs_bytes)

    with tarfile.open(os.path.join(ALP_DIR, "tar.apk"), "r:gz") as tf:
        gtar_bytes = tf.extractfile("bin/tar").read()
        cpio.add("usr/local/bin/gtar", 0o100755, gtar_bytes)

    with tarfile.open(os.path.join(ALP_DIR, "acl-libs.apk"), "r:gz") as tf:
        for m in tf.getmembers():
            if m.name.startswith("usr/lib/libacl.so"):
                if m.issym():
                    cpio.add(m.name, 0o120777, m.linkname.encode("utf-8"))
                elif m.isfile():
                    cpio.add(m.name, 0o100755, tf.extractfile(m).read())

    with tarfile.open(os.path.join(ALP_DIR, "libcrypto3.apk"), "r:gz") as tf:
        crypto_bytes = tf.extractfile("usr/lib/libcrypto.so.3").read()
        cpio.add("usr/lib/libcrypto.so.3", 0o100755, crypto_bytes)

    # 3. Custom /init script executing exact mkiosroot.sh + VirtualAudio disable + fsck.hfsplus
    init_sh = """#!/bin/sh
export PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH=/usr/lib:/lib
/bin/busybox mkdir -p /bin /sbin /usr/bin /usr/sbin /proc /sys /dev /mnt /tmp
/bin/busybox --install -s

mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

on_exit() {
    rc=$?
    echo "[init] Exiting with code $rc"
    sync
    poweroff -f
}
trap on_exit EXIT

echo "[init] Loading kernel modules..."
ln -sf /bin/kmod /sbin/modprobe
ln -sf /bin/kmod /sbin/insmod
/sbin/modprobe virtio_pci
/sbin/modprobe virtio_blk
/sbin/modprobe nls_utf8
/sbin/insmod /modules/cdrom.ko
/sbin/insmod /modules/hfsplus.ko

for i in 1 2 3 4 5 6 7 8 9 10; do
    [ -b /dev/vda ] && [ -b /dev/vde ] && break
    mdev -s 2>/dev/null || true
    sleep 1
done
ls -l /dev/vd*

set -e

echo "[init] Formatting /dev/vda as case-sensitive journaled HFS+ (iOSRoot)..."
/usr/sbin/mkfs.hfsplus -s -J -v iOSRoot /dev/vda

echo "[init] Mounting /dev/vda on /mnt..."
mount -t hfsplus -o force /dev/vda /mnt

echo "[init] Extracting os.tar (/dev/vdb)..."
/usr/local/bin/gtar --numeric-owner -xpSf /dev/vdb -C /mnt

echo "[init] Extracting sysos.tar (/dev/vdc)..."
/usr/local/bin/gtar --numeric-owner -xpSf /dev/vdc -C /mnt

echo "[init] Extracting appos.tar (/dev/vdd)..."
/usr/local/bin/gtar --numeric-owner -xpSf /dev/vdd -C /mnt

echo "[init] Applying tweaks.tar (/dev/vde)..."
/usr/local/bin/gtar --numeric-owner --overwrite -xpSf /dev/vde -C /mnt

# Guarantee exact owners/modes on logd & keybag dirs (mkiosroot.sh lines 116-128)
mkdir -p /mnt/private/var/db/diagnostics /mnt/private/var/db/uuidtext /mnt/private/var/db/diagnostics/timesync /mnt/private/var/keybags
chown 272:272 /mnt/private/var/db/diagnostics /mnt/private/var/db/uuidtext /mnt/private/var/db/diagnostics/timesync
chmod 0751 /mnt/private/var/db/diagnostics
chmod 0755 /mnt/private/var/db/uuidtext /mnt/private/var/db/diagnostics/timesync
for d in diagnostics uuidtext diagnostics/timesync; do
    : > "/mnt/private/var/db/$d/.migrated"
    chown 272:272 "/mnt/private/var/db/$d/.migrated"
    chmod 0644 "/mnt/private/var/db/$d/.migrated"
done
chown 0:0 /mnt/private/var/keybags /mnt/private/var/keybags/systembag.kb
chmod 0700 /mnt/private/var/keybags
chmod 0600 /mnt/private/var/keybags/systembag.kb

echo "[init] Disabling VirtualAudio.plugin..."
if [ -e /mnt/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin ]; then
    mkdir -p /mnt/Library/Audio/Plug-Ins/HAL.disabled
    mv /mnt/Library/Audio/Plug-Ins/HAL/VirtualAudio.plugin /mnt/Library/Audio/Plug-Ins/HAL.disabled/
    echo "[init] Moved VirtualAudio.plugin -> HAL.disabled/"
fi

echo "[init] Hard-linking dyld shared cache..."
dsc=private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld
mkdir -p /mnt/System/Library/Caches/com.apple.dyld
for f in /mnt/$dsc/*; do
    ln "$f" "/mnt/System/Library/Caches/com.apple.dyld/${f##*/}"
done

echo "[init] Hard-linking Cryptexes (OS, App) into root..."
linked_cnt=0
for c in OS App; do
    (cd "/mnt/private/preboot/Cryptexes/$c" && find . ! -type d ! -path './System/Library/Caches/com.apple.dyld/*') |
    while IFS= read -r f; do
        f=${f#./}
        if [ ! -e "/mnt/$f" ] && [ ! -L "/mnt/$f" ]; then
            mkdir -p "/mnt/$(dirname "$f")"
            src="/mnt/private/preboot/Cryptexes/$c/$f"
            if [ -L "$src" ]; then
                cp -P "$src" "/mnt/$f"
            else
                ln "$src" "/mnt/$f"
            fi
        fi
    done
done

echo "[init] Verifying volume usage and critical iOS 16 files..."
df -h /mnt
ls -la /mnt/sbin/launchd
ls -la /mnt/System/Library/Caches/com.apple.dyld
ls -la /mnt/private/var/keybags/systembag.kb
ls -la /mnt/private/var/mobile/Library/Preferences

echo "[init] Syncing and unmounting /mnt..."
sync
umount /mnt

echo "[init] Running fsck.hfsplus -f -y /dev/vda..."
/usr/sbin/fsck.hfsplus -f -y /dev/vda | grep -v 'Illegal name' || true

echo "[init] Running final verification fsck.hfsplus -f -n /dev/vda..."
/usr/sbin/fsck.hfsplus -f -n /dev/vda

echo "=== HFSPLUS_BUILD_SUCCESS ==="
""".encode("utf-8")
    cpio.add("init", 0o100755, init_sh)

    base_initramfs = open(os.path.join(ALP_DIR, "initramfs-lts"), "rb").read()
    overlay_gz = cpio.finish_gzip()
    with open(out_initramfs, "wb") as f:
        f.write(base_initramfs)
        f.write(overlay_gz)
    print(f"[initramfs] Built {out_initramfs} ({len(base_initramfs) + len(overlay_gz)} bytes)")


def main():
    os_tar = os.path.join(IPSW_DIR, "os.tar")
    sysos_tar = os.path.join(IPSW_DIR, "sysos.tar")
    appos_tar = os.path.join(IPSW_DIR, "appos.tar")
    tweaks_tar = os.path.join(IPSW_DIR, "tweaks.tar")
    initramfs_out = os.path.join(ALP_DIR, "initramfs-hfsbuilder.img")
    out_img = os.path.join(IPSW_DIR, "ios16-root.img")

    if not os.path.exists(tweaks_tar):
        build_tweaks_tar(os_tar, sysos_tar, tweaks_tar)
    else:
        print(f"[tweaks] Reusing existing {tweaks_tar}", flush=True)
    build_hfs_initramfs(initramfs_out)

    # Create fresh 16 GiB sparse raw file on NTFS (set sparse flag BEFORE extending EOF!)
    if os.path.exists(out_img):
        os.remove(out_img)
    open(out_img, "wb").close()
    subprocess.run(["fsutil", "sparse", "setflag", out_img], check=False)
    with open(out_img, "r+b") as f:
        f.truncate(16 * 1024 * 1024 * 1024)
    print(f"[img] Created 16 GiB sparse raw target: {out_img}", flush=True)

    qemu = r"C:\Program Files\qemu\qemu-system-x86_64.exe"
    vmlinuz = os.path.join(ALP_DIR, "vmlinuz-lts")
    cmd = [
        qemu,
        "-M", "pc",
        "-accel", "tcg,thread=multi",
        "-cpu", "max",
        "-smp", "4",
        "-m", "3072",
        "-nic", "none",
        "-nographic",
        "-no-reboot",
        "-kernel", vmlinuz,
        "-initrd", initramfs_out,
        "-append", "console=ttyS0 quiet panic=1",
        "-drive", f"file={out_img},format=raw,if=virtio,cache=unsafe",
        "-drive", f"file={os_tar},format=raw,if=virtio,readonly=on",
        "-drive", f"file={sysos_tar},format=raw,if=virtio,readonly=on",
        "-drive", f"file={appos_tar},format=raw,if=virtio,readonly=on",
        "-drive", f"file={tweaks_tar},format=raw,if=virtio,readonly=on",
    ]
    print("+", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    success = False
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if "=== HFSPLUS_BUILD_SUCCESS ===" in line:
            success = True
    proc.wait()
    if not success:
        raise SystemExit("ERROR: HFS+ build inside QEMU did not report HFSPLUS_BUILD_SUCCESS!")
    print("[SUCCESS] 16 GiB HFS+ iOS 16.7.16 root image built and fsck-verified!", flush=True)


if __name__ == "__main__":
    main()
