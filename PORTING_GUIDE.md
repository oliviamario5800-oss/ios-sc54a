# Step-by-Step Porting & Bring-Up Guide: ios-sc54a (No-TWRP Workflow)

This guide walks through bringing up Apple's iOS 16 (unmodified iPhone X userland + XNU kernel) on the **Samsung Galaxy A51 5G** (NTT Docomo **SC-54A** / au **SCG07** / codename **`a51xq`**, Snapdragon 765G).

> [!IMPORTANT]
> **TWRP is NOT required (and not available):**
> Samsung Galaxy A51 5G Japanese carrier models (`SC-54A`, `SCG07`) have no official or stable TWRP builds.
> This port is specifically engineered to **bypass the need for custom recovery entirely**, utilizing Samsung's official **Download Mode + Odin** to flash both `boot` and `userdata` (iOS rootfs) partitions directly from your PC.

---

## Bootloader & Flashing Prerequisites

1. **Bootloader Unlocking on Japanese Carrier Variants**:
   - NTT Docomo (`SC-54A`) and au (`SCG07`) models typically do not expose the "OEM Unlocking" toggle in Developer Options.
   - Flashing custom binaries through Odin requires the bootloader to be in an unlocked state (via Qualcomm 9008 EDL Mode Firehose programmer for SM7250, engineering bootloader, or developer board).
   - If using global variants (`SM-A5160`, `SM-A516N`, `SM-A516B`), unlock the bootloader normally via Developer Options $\rightarrow$ Download Mode unlock prompt.
2. **PC Tools**:
   - Windows with **Samsung Odin v3.14.4** (or Linux with **Heimdall**)
   - Samsung Mobile USB Drivers installed
   - Python 3.8+ (for packing tools and ADT builder)

---

## Step 1: Build the XNU Kernel

Build the kernel via GitHub Actions on macOS runners:
```sh
gh workflow run xnu.yml -R YOUR_USERNAME/ios-sc54a --field machine_config=SC54A
gh run download RUN_ID -R YOUR_USERNAME/ios-sc54a -n xnu-kernel -D xnu/out
```

---

## Step 2: Build the Apple Device Tree (ADT) and Payload

1. **Generate the Apple Device Tree**:
```sh
mkdir -p shim/out
python tools/mkadt.py shim/out/sc54a.adt --highmem
```

2. **Package the Payload**:
```sh
python tools/mkpayload.py \
    xnu/out/kernel.development.sc54a \
    shim/out/sc54a.adt \
    shim/out/payload.bin
```

---

## Step 3: Build the Boot Image and All-In-One Odin Tarball

The bare-metal shim embeds the XNU payload directly behind `_end` in the kernel `Image`, ensuring Samsung's bootloader never strips it:

```sh
# On Linux / WSL:
bash shim/build.sh shim/out/payload.bin
```

### Packaging for Odin (No TWRP Needed!)
Use [`tools/sc54a-odin-pack.py`](file:///C:/Users/Owner/Projects/ios-sc54a/tools/sc54a-odin-pack.py) to package partition images into Odin archives:

```sh
# Option 1: Pack individual boot archive
python tools/sc54a-odin-pack.py shim/out/boot.img shim/out

# Option 2: Pack iOS rootfs into userdata archive
python tools/sc54a-odin-pack.py ios16-root.img shim/out

# Option 3 (Recommended): ALL-IN-ONE single flash package!
python tools/sc54a-odin-pack.py \
    --boot shim/out/boot.img \
    --userdata ios16-root.img \
    --out shim/out/AP_ios_sc54a.tar.md5
```

---

## Step 4: Flash Everything via Odin (Download Mode)

1. **Enter Download Mode**:
   - Power off the device completely.
   - Hold `Volume Up` + `Volume Down` and connect the USB-C cable to your PC.
   - The cyan "Warning" screen will appear; press `Volume Up` to enter Download Mode.
2. **Flash with Odin**:
   - Launch **Odin3 v3.14.4**.
   - Check that the `ID:COM` box turns light blue.
   - Click the **AP** slot button and select `shim/out/AP_ios_sc54a.tar.md5` (or `boot.tar.md5` in AP and `userdata.tar.md5` in USERDATA).
   - In the "Options" tab of Odin, ensure **Auto Reboot** and **F. Reset Time** are checked.
   - Click **Start**.
   - Odin will flash `boot` and `userdata` directly to the phone's UFS storage blocks.

---

## Step 5: Booting iOS 16

1. The device reboots automatically:
   - Samsung boot logo appears briefly.
   - The bare-metal shim executes and initializes early MMU / GICv3.
   - Screen clears and the Apple logo appears.
2. The initial boot takes approximately 3–4 minutes while iOS generates system caches.
3. The iOS 16 Lock Screen appears on the 1080x2400 Super AMOLED panel!
