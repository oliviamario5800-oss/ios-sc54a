# Step-by-Step Porting & Bring-Up Guide: ios-sc54a

This guide walks through bringing up Apple's iOS 16 (unmodified iPhone X userland + XNU kernel) on the **Samsung Galaxy A51 5G** (NTT Docomo **SC-54A** / au **SCG07** / codename **`a51xq`**, Snapdragon 765G).

---

## Prerequisites

1. **Hardware**:
   - Samsung Galaxy A51 5G (`SC-54A` or `SCG07` or `SM-A516N/0/B/U`) with **unlocked bootloader**.
   - USB-C cable and PC running Windows (with Odin) or Linux (with Heimdall & fastboot/adb).
2. **Software & Build Toolchains**:
   - `python3` with `pycryptodome`
   - `aarch64-linux-gnu-gcc` cross-compiler
   - `mkbootimg`
   - `tar` & `md5sum`
   - Stock firmware files or TWRP recovery for partition backups.
   - iPhone X iOS 16.7.16 (20H392) IPSW from Apple.

---

## Step 0: Backup Device Calibration & Partitions

Before flashing anything to `userdata` or `boot`, boot TWRP recovery and pull vital per-device calibration data:
```sh
adb root
adb shell
# Back up unique partition calibration data:
dd if=/dev/block/by-name/persist of=/tmp/persist.img
dd if=/dev/block/by-name/modemst1 of=/tmp/modemst1.img
dd if=/dev/block/by-name/modemst2 of=/tmp/modemst2.img
dd if=/dev/block/by-name/fsg of=/tmp/fsg.img
dd if=/dev/block/by-name/fsc of=/tmp/fsc.img
dd if=/dev/block/by-name/boot of=/tmp/boot_stock.img
dd if=/dev/block/by-name/dtbo of=/tmp/dtbo_stock.img
dd if=/dev/block/by-name/vbmeta of=/tmp/vbmeta_stock.img
exit
adb pull /tmp/ .
```

Unpack the stock DTB from your boot image:
```sh
python3 tools/unpack_bootv2.py boot_stock.img dtb/boot_unpacked
```

---

## Step 1: Build the XNU Kernel

The kernel builds on macOS runners (macos-14 with Xcode 15) via GitHub Actions using the `.github/workflows/xnu.yml` workflow:

```sh
# Trigger workflow dispatch for SC54A target
gh workflow run xnu.yml -R YOUR_USERNAME/ios-sc54a --field machine_config=SC54A
# Download compiled kernel artifact
gh run download RUN_ID -R YOUR_USERNAME/ios-sc54a -n xnu-kernel -D xnu/out
```

The resulting binary will be `xnu/out/kernel.development.sc54a` (or `kernel.development.sweet`).

---

## Step 2: Build the Apple Device Tree (ADT) and Payload

1. **Generate the Apple Device Tree**:
```sh
mkdir -p shim/out
python3 tools/mkadt.py shim/out/sc54a.adt --highmem
```

2. **Package the XNU Kernel, ADT, and Boot Arguments**:
```sh
python3 tools/mkpayload.py \
    xnu/out/kernel.development.sc54a \
    shim/out/sc54a.adt \
    shim/out/payload.bin \
    --cmdline 'debug=0xc serial=3 cpus=6 keepsyms=1 -enable_kprintf_spam -noprogress rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 sc54afb_noconsole=1 sc54afb_hz=30'
```

---

## Step 3: Build the Boot Shim and Odin Tarball

1. **Compile the Bare-Metal Shim and Embed the Payload**:
```sh
bash shim/build.sh shim/out/payload.bin
```

This generates:
- `shim/out/Image`: Low-level kernel image with embedded payload behind `_end`.
- `shim/out/boot.img`: Complete Android boot partition image.
- `shim/out/boot.tar`: Odin-compatible archive ready for flashing.
- `shim/out/boot.tar.md5`: Verified Odin archive with MD5 footer.

2. If building on Windows manually:
```sh
python tools/sc54a-odin-pack.py shim/out/boot.img shim/out
```

---

## Step 4: Create the iOS 16 Rootfs on HFS+

Extract the system disks from the iPhone X iOS 16.7.16 IPSW on Linux:

```sh
# Assemble the unified HFS+ filesystem image (16 GB):
tools/mkiosroot.sh OS.dmg SystemOS.dmg AppOS.dmg ios16-root.img 16G

# Check and mount HFS+ image:
sudo fsck.hfsplus -fy ios16-root.img
mkdir -p /mnt/iosroot
sudo mount -t hfsplus -o loop,force ios16-root.img /mnt/iosroot

# Apply bring-up adjustments (disable crash-looping daemons, skip setup):
sudo python3 tools/sc54a-root-tweaks.py /mnt/iosroot

# Unmount and convert to sparse image for flashing:
sudo umount /mnt/iosroot
img2simg ios16-root.img ios16-root.simg
```

---

## Step 5: Flash to SC-54A

### Method A: Flashing via Odin (Windows / Download Mode)
1. Boot phone into **Download Mode**:
   - Power off device.
   - Hold `Volume Up` + `Volume Down` and connect USB cable to PC.
   - Press `Volume Up` to enter Download Mode.
2. Open **Odin v3.14.4**:
   - Verify COM port lights up blue in Odin.
   - Load `shim/out/boot.tar.md5` into the **AP** slot.
   - (Ensure an empty or AVB-disabled `vbmeta.tar` is flashed in `USERDATA` or `CSC` if required by Knox).
   - Click **Start** to flash the boot partition.
3. Flash the iOS rootfs into the `userdata` partition:
   - Boot into TWRP or fastbootd mode:
     ```sh
     fastboot flash userdata ios16-root.simg
     ```
   - Or write directly via TWRP adb shell:
     ```sh
     adb push ios16-root.img /sdcard/
     adb shell "dd if=/sdcard/ios16-root.img of=/dev/block/by-name/userdata bs=4M"
     ```

### Method B: Flashing via TWRP Recovery
If you have TWRP installed on your SC-54A:
```sh
adb push shim/out/boot.img /sdcard/
adb push ios16-root.img /sdcard/
adb shell
dd if=/sdcard/boot.img of=/dev/block/by-name/boot
dd if=/sdcard/ios16-root.img of=/dev/block/by-name/userdata bs=4M
reboot
```

---

## Step 6: Initial Boot & Diagnostics

1. **Boot Screen**:
   - Phone boots: Samsung splash screen $\rightarrow$ Bare-metal shim initializes $\rightarrow$ Screen clears $\rightarrow$ Apple logo appears.
   - First boot takes **3 to 4 minutes** while iOS prepares caches, preferences, and data stores.
2. **Lock Screen**:
   - The iOS 16 Lock Screen appears (1125x2436 canvas centered on 1080x2400 AMOLED).
   - Swipe up to enter Home Screen.
3. **Reading Kernel Logs**:
   To debug any panic or inspect daemon output, reboot into TWRP recovery and pull the 4 MiB persistent console ring from `userdata`:
   ```sh
   tools/sc54a-log-pull.sh logs/boot.txt
   ```
