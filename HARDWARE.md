# Hardware Reference: Samsung Galaxy A51 5G (SC-54A) vs Redmi Note 10 Pro (sweet)

This document details the hardware mapping, register offsets, and architectural comparisons for porting **ios-sweet** (Apple XNU xnu-8796 + iOS 16.7.16) to the **Samsung Galaxy A51 5G** (NTT Docomo **SC-54A** / au **SCG07** / codename **`a51xq`**).

---

## 1. System-on-Chip (SoC) & CPU Architecture

| Property | Xiaomi Redmi Note 10 Pro (`sweet`) | Samsung Galaxy A51 5G (`SC-54A`) | Notes |
| :--- | :--- | :--- | :--- |
| **SoC** | Qualcomm Snapdragon 732G (`SM7150`) | Qualcomm Snapdragon 765G (`SM7250`) | Both 8nm Samsung LPP / EUV |
| **CPU Microarchitecture** | Kryo 470 (2x Cortex-A76 + 6x Cortex-A55) | Kryo 475 (1x Prime + 1x Gold + 6x Silver) | ARMv8.2-A + Cryptography Extensions |
| **Core Layout** | 6x Silver @ 1.80 GHz<br>2x Gold @ 2.30 GHz | 6x Silver @ 1.80 GHz<br>1x Gold @ 2.20 GHz<br>1x Prime @ 2.40 GHz | Tri-cluster architecture on SM7250 |
| **MPIDR Aff1 Assignment** | Core 0–5: `0x000`–`0x500`<br>Core 6–7: `0x600`–`0x700` | Core 0–5: `0x000`–`0x500`<br>Core 6: `0x600`<br>Core 7: `0x700` | Core affinity mapping is identical |
| **16 KB Granule (`TGran16`)** | Supported on all 8 cores (`ID_AA64MMFR0.TGran16 = 1`) | Supported on all 8 cores (`ID_AA64MMFR0.TGran16 = 1`) | **Zero instruction emulation needed** (unlike SM6225/A53) |
| **Instruction Set** | ARMv8.2-A | ARMv8.2-A | Full support for LSE atomics, RCpc, PAN |

---

## 2. Memory & Physical Address Mapping

| Subsystem | `sweet` (SM7150) | `SC-54A` (SM7250) | Purpose in XNU / Shim |
| :--- | :--- | :--- | :--- |
| **Total System RAM** | 6 GB or 8 GB LPDDR4X | 6 GB LPDDR4X | `SC-54A` Docomo is standard 6 GB |
| **Low DDR Bank** | `0x80000000` (2 GiB) | `0x80000000` (2 GiB) | Contains Qualcomm TZ, modem, dynamic pools |
| **High DDR Bank (`--highmem`)**| `0x100000000` + `0xfcc00000` (~3.95 GiB) | `0x100000000` + `0xfcc00000` (~3.95 GiB) | **Clean, contiguous RAM space above 4 GB** |
| **Kernel `PHYS_BASE`** | `0x100000000` | `0x100000000` | XNU physical base in highmem |
| **Panic Log Region (`/pram`)** | Top 1 MiB of DRAM bank | Top 1 MiB of DRAM bank | Exposed via `/chosen/embedded-panic-log-size` |
| **Continuous Splash (FB)** | **`0x9c000000`** | **`0xa0000000`** | `cont_splash_region` reserved memory |
| **Framebuffer Size** | `0x1700000` (~23 MiB) | `0x02300000` (~35 MiB) | Handoff buffer from ABL / sboot |

---

## 3. Peripheral & Hardware Controllers

| Controller | `sweet` (SM7150) | `SC-54A` (SM7250) | Function |
| :--- | :--- | :--- | :--- |
| **Interrupt Controller (GICv3)** | GICD: `0x17a00000` (64K)<br>GICR: `0x17a60000` (1MB) | GICD: `0x17a00000` (64K)<br>GICR: `0x17a60000` (1MB) | Identical GICv3 registers |
| **Generic Timer Frequency** | 19.2 MHz (`TIMEBASE = 19200000`) | 19.2 MHz (`TIMEBASE = 19200000`) | ARM generic arch timer |
| **Virtual Timer IRQ** | PPI 19 (`VTIMER_INTID = 19`) | PPI 19 (`VTIMER_INTID = 19`) | Mapped in GIC Group 1 |
| **Storage Controller (UFS)** | `ufshc@1d84000` (`0x01d84000`, 12K) | `ufshc@1d84000` (`0x01d84000`, 12K) | UFS 2.1 Host Controller |
| **IMEM Restart Reason** | `0x146aa65c` (`0x146aa000 + 0x65c`) | **`0x146ab65c`** (`0x146ab000 + 0x65c`) | Shared scratchpad for boot mode flags |
| **Power Supply Hold (`PS_HOLD`)**| `0x0c264000` | `0x0c264000` | PMIC power-off / reset trigger |
| **PMIC Architecture** | PM6150 + PM6150L (SPMI) | PM7250 + PM7250B (SPMI) | Power management & hardware buttons |

---

## 4. Display & Touchscreen Specifications

| Subsystem | Specifications | Configuration in Port |
| :--- | :--- | :--- |
| **Panel Type** | 6.5-inch Super AMOLED Infinity-O | Continuous video-mode scanout |
| **Resolution** | 1080 x 2400 pixels (20:9 ratio) | `FB_W = 1080`, `FB_H = 2400`, `FB_STRIDE = 4320` |
| **Pixel Density** | 405 ppi | Configured as `dot-pitch = 405` in device tree |
| **iOS Target Canvas** | iPhone X (`D22AP`) presentation | 1125 x 2436 scaled/centered by `SweetSurface` / `AppleCLCD` |
| **Touch Controller** | Samsung TSP (`sec_ts` / STMicroelectronics / Synaptics) | I2C over Qualcomm QUP GENI Serial Engine |

---

## 5. Boot Protocol & Packaging Difference

Unlike Xiaomi devices which permit tethered RAM boots via `fastboot boot shim-boot.img`:
* **Samsung Bootloader**: Samsung's bootloader does not implement fastboot tethered RAM booting. Custom kernel images must be flashed to the `boot` partition.
* **Flashing Mechanism**:
  1. **Download Mode (Odin / Heimdall)**: Image is packed into a standard USTAR archive (`boot.tar` or `boot.tar.md5`) and flashed to the **AP** slot.
  2. **Recovery Mode (TWRP)**: Image can be flashed directly to `/dev/block/by-name/boot`.
* **Payload Location**: To ensure the payload is never discarded by Samsung's bootloader when parsing ramdisk images, the loader supports **embedded payloads** placed directly behind `_end` in the kernel `Image`.
