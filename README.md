# ios-sc54a

**Apple's iOS 16 running on the Samsung Galaxy A51 5G (SC-54A).**

This project ports Apple's open-source XNU kernel (`xnu-8796`, iOS 16 generation) to the **Samsung Galaxy A51 5G** (NTT Docomo **SC-54A** / au **SCG07** / codename **`a51xq`**, powered by the Qualcomm **Snapdragon 765G** / `SM7250`). On top of it runs the **unmodified iOS 16.7.16 (20H392) userland of the iPhone X**: `launchd`, system daemons, `backboardd`, `SpringBoard`, and stock applications.

Based on [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) and HackiOS.

---

## Hardware Highlights (SM7250 vs SM7150)

- **Native 16KB Pages**: All 8 Kryo 475 cores (1x Prime Cortex-A76 @ 2.4GHz, 1x Gold Cortex-A76 @ 2.2GHz, 6x Silver Cortex-A55 @ 1.8GHz) support 16K translation granules (`TGran16`) natively in hardware. No instruction emulation or core disabling is required.
- **Continuous Splash Handoff**: The Samsung bootloader leaves the 1080x2400 Super AMOLED panel running from the `cont_splash_region` at `0xa0000000` (size `0x02300000`).
- **Memory Map**: Payload and kernel run in the clean upper DDR bank at `0x100000000` (~3.95 GiB), avoiding Qualcomm low-memory secure carveouts.
- **Samsung Flashing Support**: Integrated packaging for Odin / Heimdall flashing (`boot.tar` and `boot.tar.md5`) and embedded payload support behind `_end` for Samsung Knox bootloader compatibility.

---

## Status / What Works

| Subsystem | Status & Implementation Details |
| :--- | :--- |
| **Boot Protocol** | Stock Samsung bootloader $\rightarrow$ `boot` partition shim $\rightarrow$ XNU kernel. Packaged as Odin `boot.tar` |
| **CPU** | Up to all 8 cores via PSCI, 16K translation pages, ARMv8.2-A + Crypto |
| **Interrupts & Timer** | ARM GICv3 (`0x17a00000` / `0x17a60000`), 19.2 MHz generic arch timer, PPI 19 |
| **Memory** | ~3.95 GiB upper DDR bank above 4 GiB (`0x100000000` + `0xfcc00000`) |
| **Storage** | UFS 2.1 host driver (`0x01d84000`), iOS root on journaled HFS+ in `userdata` |
| **Display** | Kernel `SweetSurface` / `AppleCLCD` service rendering via software CoreAnimation, presented as an iPhone X (1125x2436) canvas on 1080x2400 AMOLED |
| **GUI & SpringBoard** | `launchd`, `backboardd`, `SpringBoard`, Lock Screen, Home Screen, Control Center, Settings |
| **Device Identity** | iPhone X (`iPhone10,3` / `D22AP`) gesture navigation UI |
| **Activation** | Offline "Hactivation" path via device-tree security status |

---

## Repository Layout

| Path | Description |
| :--- | :--- |
| `shim/` | Bare-metal boot shim (C + asm) configured for SM7250 / SC-54A |
| `tools/mkadt.py` | Generates Apple Device Tree (ADT) from SC-54A MMIO map and iPhone X identity |
| `tools/mkpayload.py` | Combines compiled XNU Mach-O + ADT + boot arguments into binary payload |
| `tools/sc54a-odin-pack.py` | Packages `boot.img` into Odin flashable `boot.tar` and `boot.tar.md5` |
| `tools/sc54a-root-tweaks.py` | Customizes iOS 16 rootfs (skip setup, disable watchdogs, seed activation) |
| `tools/sc54a-log-pull.sh` | Pulls persistent 4 MiB kernel log ring from device `userdata` via TWRP |
| `xnu/patches/` | Full patch series for building XNU 8796 with SC-54A board configuration |
| `.github/workflows/xnu.yml` | GitHub Actions workflow to build XNU on macOS runners |
| `HARDWARE.md` | In-depth hardware comparison between SC-54A and sweet |
| `PORTING_GUIDE.md` | Detailed step-by-step flashing and bring-up instructions |

---

## Quick Build Commands

```sh
# 1. Generate Apple Device Tree for SC-54A:
python3 tools/mkadt.py shim/out/sc54a.adt --highmem

# 2. Package Payload (Kernel + ADT + Cmdline):
python3 tools/mkpayload.py \
    xnu/out/kernel.development.sc54a \
    shim/out/sc54a.adt \
    shim/out/payload.bin

# 3. Build Shim Image and Odin Tarball:
bash shim/build.sh shim/out/payload.bin
# Or package boot.tar on Windows:
python3 tools/sc54a-odin-pack.py shim/out/boot.img shim/out

# 4. Flash boot.tar via Odin AP slot in Download Mode, then boot!
```

---

## Documentation

- See [HARDWARE.md](HARDWARE.md) for register offsets, memory map, and SoC differences.
- See [PORTING_GUIDE.md](PORTING_GUIDE.md) for complete installation, rootfs preparation, and Odin flashing steps.

---

## Credits

- [dipa-studio/ios-sweet](https://github.com/dipa-studio/ios-sweet) for the foundational XNU on Android bring-up.
- [HackiOS](https://github.com/oosexgang/hackios-xnu-kernel) by OOSEX GANG.
- Apple for open-source XNU, IOHIDFamily, and libpthread releases.
