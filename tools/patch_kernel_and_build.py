import subprocess, struct, os, sys, shutil

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TOOLS_DIR)
ZIG = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"
KERNEL_BAK = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a.bak")
KERNEL_TARGET = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a")

C_SRC = r"""
#include <stdint.h>
#include <stdbool.h>

typedef unsigned long vm_offset_t;
typedef unsigned long vm_size_t;

#define K_ML_IO_MAP                  0xfffffe0007490300UL
#define K_PE_PARSE_BOOT_ARGN         0xfffffe0007a77438UL
#define K_PRINTF                     0xfffffe000734984cUL
#define K_DELAY                      0xfffffe000731f264UL
#define K_CLOCK_INTERVAL_TO_DEADLINE 0xfffffe000731f274UL
#define K_ASSERT_WAIT_DEADLINE       0xfffffe000735478cUL
#define K_THREAD_BLOCK               0xfffffe0007354e80UL
#define K_GENI_I2C_XFER              0xfffffe00074eaddcUL
#define K_SWEET_HID_TOUCH_REPORT     0xfffffe00074eacf8UL

#define SE_STATUS       0x040u
#define SE_MCLK         0x048u
#define SE_CLK_SEL      0x07cu
#define SE_DMA          0x258u
#define SE_SCL          0x278u
#define SE_IRQ_CLR      0x618u
#define SE_IRQ_EN       0x614u

#define TOUCH_MAX 10

static int touch_sleep_chan;
static uint32_t active_mask;

static inline uint32_t mmio_rd(vm_offset_t base, uint32_t off) {
    uint32_t v = *(volatile uint32_t *)(base + off);
    __asm__ volatile("dmb oshld" ::: "memory");
    return v;
}

static inline void mmio_wr(vm_offset_t base, uint32_t off, uint32_t val) {
    __asm__ volatile("dmb oshst" ::: "memory");
    *(volatile uint32_t *)(base + off) = val;
    __asm__ volatile("dsb osh" ::: "memory");
}

static inline void delay_us(int usec) {
    ((void (*)(int))K_DELAY)(usec);
}

static void touch_wait_ms(unsigned ms) {
    uint64_t deadline;
    ((void (*)(uint32_t, uint64_t, uint64_t *))K_CLOCK_INTERVAL_TO_DEADLINE)(ms, 1000000ULL, &deadline);
    ((void (*)(void *, int, uint64_t))K_ASSERT_WAIT_DEADLINE)(&touch_sleep_chan, 0, deadline);
    ((void (*)(void *))K_THREAD_BLOCK)(0);
}

static void touch_gpio_cfg(vm_offset_t gpio_base, unsigned pin, unsigned mux, unsigned pull, unsigned drive, bool out) {
    uint32_t off = pin * 0x1000;
    uint32_t cfg = mmio_rd(gpio_base, off);
    if (out) {
        mmio_wr(gpio_base, off + 4, mmio_rd(gpio_base, off + 4) | 2u);
    }
    cfg &= ~0x3ffu;
    cfg |= (mux << 2) | pull | (drive << 6) | (out ? 0x200u : 0u);
    mmio_wr(gpio_base, off, cfg);
}

static void touch_se_init(vm_offset_t se) {
    mmio_wr(se, 0xe18, 0);
    mmio_wr(se, 0xe1c, 0);
    mmio_wr(se, 0x644, 0);
    mmio_wr(se, SE_IRQ_CLR, 0xffffffffu);
    mmio_wr(se, 0x648, 0xffffffffu);
    mmio_wr(se, SE_DMA, 0);
    mmio_wr(se, 0x28, mmio_rd(se, 0x28) | 0x7fu);
    mmio_wr(se, 0xe30, mmio_rd(se, 0xe30) | 0x0fu);
    mmio_wr(se, 0x24, 0x7f);
    mmio_wr(se, 0x20, 1);
    mmio_wr(se, SE_CLK_SEL, 0);
    mmio_wr(se, SE_MCLK, 0x71);
    mmio_wr(se, SE_SCL, 0x00a02c1a);
    mmio_wr(se, 0x254, 0);
    mmio_wr(se, 0x260, 0x0007f8fe);
    mmio_wr(se, 0x264, 0x000ffefe);
    mmio_wr(se, 0x284, 0x0007f8fe);
    mmio_wr(se, 0x288, 0x000ffefe);
    mmio_wr(se, 0x80c, 0);
    mmio_wr(se, 0x810, 0);
    mmio_wr(se, 0x814, 15);
    mmio_wr(se, SE_IRQ_EN, 0x7f00363fu);
}

static int mms_read_reg2(uint8_t addr, uint8_t r0, uint8_t r1, uint8_t *buf, unsigned len) {
    uint8_t cmd[2] = { r0, r1 };
    *(volatile uint8_t *)0xfffffe0008052831UL = 0;
    int err = ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_GENI_I2C_XFER)(addr, cmd, 2, 0, 0, true);
    if (err) return err;
    *(volatile uint8_t *)0xfffffe0008052831UL = 0;
    return ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_GENI_I2C_XFER)(addr, 0, 0, buf, len, false);
}

static int mms_write_reg2(uint8_t addr, uint8_t r0, uint8_t r1, uint8_t val) {
    uint8_t cmd[3] = { r0, r1, val };
    *(volatile uint8_t *)0xfffffe0008052831UL = 0;
    return ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_GENI_I2C_XFER)(addr, cmd, 3, 0, 0, false);
}

static void mms_report(uint32_t mask, const int *x, const int *y) {
    ((void (*)(uint32_t, const int *, const int *))K_SWEET_HID_TOUCH_REPORT)(mask, x, y);
}

static int mms_poll(void) {
    uint8_t pkt_info = 0;
    int err = mms_read_reg2(0x48, 0x02, 0x10, &pkt_info, 1);
    if (err) return err;

    uint8_t count = pkt_info & 0x7fu;
    if (count == 0 || (pkt_info & 0x80u)) {
        if (active_mask != 0) {
            int dummy_x[TOUCH_MAX] = {0}, dummy_y[TOUCH_MAX] = {0};
            mms_report(0, dummy_x, dummy_y);
            active_mask = 0;
        }
        return 0;
    }

    if (count > TOUCH_MAX) count = TOUCH_MAX;
    uint8_t buf[160];
    unsigned total_len = (unsigned)count * 16u;
    err = mms_read_reg2(0x48, 0x02, 0x11, buf, total_len);
    if (err) return err;

    uint32_t mask = 0;
    int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};

    for (unsigned i = 0; i < count; i++) {
        const uint8_t *p = buf + (i * 16u);
        if ((p[0] & 0x03u) != 1u) continue; // must be finger touch event (Format 10)
        int id = ((int)(p[0] >> 2) & 0x0fu) - 1;
        if (id < 0 || id >= TOUCH_MAX) continue;
        uint8_t action = (p[0] >> 6) & 0x03u;
        bool down = (action == 1u || action == 2u);

        int raw_x = ((int)p[1] << 4) | ((int)p[3] >> 4);
        int raw_y = ((int)p[2] << 4) | ((int)p[3] & 0x0fu);
        if (raw_x > 4095) raw_x = 4095;
        if (raw_x < 0) raw_x = 0;
        if (raw_y > 4095) raw_y = 4095;
        if (raw_y < 0) raw_y = 0;

        int px = (raw_x * 1080) / 4095;
        int py = (raw_y * 2400) / 4095;
        if (px > 1079) px = 1079;
        if (py > 2399) py = 2399;

        if (down) {
            mask |= (1u << id);
            x[id] = px;
            y[id] = py;
        }
    }

    mms_report(mask, x, y);
    active_mask = mask;
    return 0;
}

void sc54a_touch_thread(void *arg, int result) {
    (void)arg; (void)result;

    /* 1. Mute console output immediately so nothing draws over GUI */
    *(volatile int *)0xfffffe0007be14c8UL = 1; // sweet_console_muted = 1
    *(volatile int *)0xfffffe0007c1e75cUL = 1; // disableConsoleOutput = 1

    /* 2. Clear any lingering early boot text from the framebuffer if mapped */
    volatile uint32_t *fb = *(volatile uint32_t **)0xfffffe0008074168UL;
    if (fb) {
        uint32_t h = *(volatile uint32_t *)0xfffffe0008074158UL;
        uint32_t stride = *(volatile uint32_t *)0xfffffe0008074170UL;
        if (h && stride) {
            uint32_t total_words = h * (stride / 4);
            for (uint32_t i = 0; i < total_words; i++) {
                fb[i] = 0;
            }
        }
    }

    unsigned delay_s = 2;
    ((int (*)(const char *, void *, int))K_PE_PARSE_BOOT_ARGN)("sweettouchdelay", &delay_s, sizeof(delay_s));
    touch_wait_ms(delay_s * 1000);

    /* Map MMIO blocks: GCC, TLMM, and QUPv3_1 SE1 (SE7) */
    vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00100000UL, 0x60000);
    vm_offset_t gpio = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x0f000000UL, 0x100000);
    vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00984000UL, 0x4000);

    if (!gcc || !gpio || !se) {
        return;
    }

    /* Set global pointers for native GENI I2C driver (0xfffffe00074eaddc) */
    *(volatile vm_offset_t *)0xfffffe0008052818UL = se;
    *(volatile vm_offset_t *)0xfffffe0008052820UL = gcc;
    *(volatile vm_offset_t *)0xfffffe0008052828UL = gpio;
    *(volatile uint8_t *)0xfffffe0008052831UL = 0; // clear error latch

    /* Enable QUPv3_1 wrapper clocks in GCC vote registers (0x52008 & 0x5200c, mask 0x01bc0000) */
    mmio_wr(gcc, 0x52008, mmio_rd(gcc, 0x52008) | 0x01bc0000u);
    mmio_wr(gcc, 0x5200c, mmio_rd(gcc, 0x5200c) | 0x01bc0000u);

    /* Configure RCG for 19.2 MHz XO on Wrapper 1 SE1 (0x18248) if not already running */
    uint32_t dfs = mmio_rd(gcc, 0x18248 + 0x14);
    if (!(dfs & 1u)) {
        uint32_t cfg = mmio_rd(gcc, 0x1824c);
        cfg = (cfg & ~(0x371fu | (1u << 20))) | 1u;
        mmio_wr(gcc, 0x1824c, cfg);
        mmio_wr(gcc, 0x18248, mmio_rd(gcc, 0x18248) | 1u);
        for (int i = 0; i < 5000; i++) {
            if (!(mmio_rd(gcc, 0x18248) & 1u)) break;
            delay_us(1);
        }
    }

    /* Enable branch CBCRs: WRAP1 M_AHB, S_AHB, CORE, CORE_2X, and SE1 (0x1826c, 0x18270) */
    mmio_wr(gcc, 0x18004, mmio_rd(gcc, 0x18004) | 1u);
    mmio_wr(gcc, 0x18008, mmio_rd(gcc, 0x18008) | 1u);
    mmio_wr(gcc, 0x1800c, mmio_rd(gcc, 0x1800c) | 1u);
    mmio_wr(gcc, 0x18010, mmio_rd(gcc, 0x18010) | 1u);
    mmio_wr(gcc, 0x1826c, mmio_rd(gcc, 0x1826c) | 1u);
    mmio_wr(gcc, 0x18270, mmio_rd(gcc, 0x18270) | 1u);

    /* Enable USB 3.0 controller clocks in GCC */
    mmio_wr(gcc, 0x1a010, mmio_rd(gcc, 0x1a010) | 1u); // MASTER CBCR
    mmio_wr(gcc, 0x1a014, mmio_rd(gcc, 0x1a014) | 1u); // SLEEP CBCR
    mmio_wr(gcc, 0x1a018, mmio_rd(gcc, 0x1a018) | 1u); // MOCK UTMI CBCR

    /* Configure TLMM GPIOs for SC-54A: GPIO 6 & 7 = qup11 (I2C), GPIO 9 = IRQ */
    touch_gpio_cfg(gpio, 6, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 7, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 9, 0, 0, 0, false);
    touch_se_init(se);

    /* Melfas MSS100 Init: Query ready status */
    uint8_t boot_st = 0;
    for (int retry = 0; retry < 10; retry++) {
        *(volatile uint8_t *)0xfffffe0008052831UL = 0;
        int pe = mms_read_reg2(0x48, 0x06, 0x00, &boot_st, 1);
        if (pe == 0 && (boot_st == 0xa0 || boot_st == 0x05)) break;
        touch_wait_ms(10);
    }

    /* Switch to ACTIVE power mode: { 0x06, 0x13, 0x00 } */
    *(volatile uint8_t *)0xfffffe0008052831UL = 0;
    mms_write_reg2(0x48, 0x06, 0x13, 0x00);
    touch_wait_ms(20);

    /* Disable ESD alert: { 0x06, 0x18, 0x01 } */
    *(volatile uint8_t *)0xfffffe0008052831UL = 0;
    mms_write_reg2(0x48, 0x06, 0x18, 0x01);
    touch_wait_ms(10);

    for (;;) {
        *(volatile uint8_t *)0xfffffe0008052831UL = 0;
        int err = mms_poll();
        if (err) {
            touch_wait_ms(15);
        } else {
            touch_wait_ms(10);
        }
    }
}
"""

def main():
    print("[1/4] Compiling Melfas MSS100 driver...")
    src_c = os.path.join(TOOLS_DIR, "mms_driver.c")
    obj_o = os.path.join(TOOLS_DIR, "mms_driver.o")
    elf_out = os.path.join(TOOLS_DIR, "mms_driver.elf")
    ld_script = os.path.join(TOOLS_DIR, "mms_driver.ld")

    with open(src_c, "w") as f:
        f.write(C_SRC)

    # Linker script placing code at 0xfffffe00074e9220
    with open(ld_script, "w") as f:
        f.write("""
SECTIONS {
    . = 0xfffffe00074e9220;
    .text : {
        *(.text*)
        *(.rodata*)
        *(.data*)
        *(.bss*)
        *(COMMON)
    }
    /DISCARD/ : {
        *(.eh_frame*)
        *(.note*)
        *(.comment*)
    }
}
""")

    subprocess.run([
        ZIG, "cc", "-target", "aarch64-freestanding-none", "-mcpu=cortex_a55",
        "-Os", "-ffreestanding", "-nostdlib", "-fno-pic", "-mstrict-align",
        "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
        "-c", src_c, "-o", obj_o
    ], check=True)

    subprocess.run([
        ZIG, "cc", "-target", "aarch64-freestanding-none",
        "-nostdlib", "-static",
        f"-Wl,-T,{ld_script.replace('\\', '/')}", "-Wl,--build-id=none",
        obj_o, "-o", elf_out
    ], check=True)

    # Read binary payload from elf
    with open(elf_out, "rb") as f:
        elf_bytes = f.read()

    e_shoff, = struct.unpack_from("<Q", elf_bytes, 40)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", elf_bytes, 58)
    sh_str = elf_bytes[e_shoff + e_shstrndx * e_shentsize : e_shoff + (e_shstrndx + 1) * e_shentsize]
    str_off, str_sz = struct.unpack_from("<QQ", sh_str, 24)
    strtab = elf_bytes[str_off : str_off + str_sz]

    text_bytes = None
    text_addr = None
    for i in range(e_shnum):
        sh = elf_bytes[e_shoff + i * e_shentsize : e_shoff + (i + 1) * e_shentsize]
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size = struct.unpack("<IIQQQQ", sh[:40])
        name = strtab[sh_name : strtab.find(b"\0", sh_name)].decode()
        if name == ".text":
            text_bytes = elf_bytes[sh_offset : sh_offset + sh_size]
            text_addr = sh_addr
            break

    assert text_bytes is not None and text_addr == 0xfffffe00074e9220, f"bad .text: addr={text_addr:#x}"
    print(f"    Compiled .text payload: {len(text_bytes)} bytes at {text_addr:#x}")

    total_driver_size = 4 + len(text_bytes)
    assert total_driver_size <= 6008, f"Driver too large: {total_driver_size} > 6008"

    print("[2/4] Patching kernel.development.sc54a...")
    kernel_data = bytearray(open(KERNEL_BAK, "rb").read())

    # 1. Patch _touch_thread:
    # At 0xfffffe00074e921c (file offset 0x4e521c):
    # Instruction: 'b 0xfffffe00074e9220' (in ARM64: 0x14000001)
    touch_entry_file_off = 0x004e521c
    struct.pack_into("<I", kernel_data, touch_entry_file_off, 0x14000001)
    # Then write text_bytes at 0x4e5220:
    kernel_data[touch_entry_file_off + 4 : touch_entry_file_off + 4 + len(text_bytes)] = text_bytes
    print(f"    Injected touch driver at offset {touch_entry_file_off:#x} ({total_driver_size} bytes)")

    # 2. Patch USB controller in sweet_usb_probe:
    nop = 0xd503201f # 'nop'
    # Check 1: VA 0xfffffe00074ec9e0 (File 0x4e89e0)
    struct.pack_into("<I", kernel_data, 0x004e89e0, nop)
    # Check 2: VA 0xfffffe00074ec9e4 (File 0x4e89e4)
    struct.pack_into("<I", kernel_data, 0x004e89e4, nop)
    # Check 3: VA 0xfffffe00074ec9e8 (File 0x4e89e8)
    struct.pack_into("<I", kernel_data, 0x004e89e8, nop)
    # Gadget start check: VA 0xfffffe00074ecaf4 (File 0x4e8af4)
    struct.pack_into("<I", kernel_data, 0x004e8af4, nop)
    # High-Speed PHY clock halt check: VA 0xfffffe00074ecb48 (File 0x4e8b48)
    struct.pack_into("<I", kernel_data, 0x004e8b48, nop)
    # High-Speed PHY base address: VA 0xfffffe00074ecb4c (File 0x4e8b4c) -> 0x088e3000
    struct.pack_into("<I", kernel_data, 0x004e8b4c, 0x52860000) # mov w0, #0x3000
    print("    Applied all 6 USB controller & FemtoPHY patches.")

    # 3. Skip IOLog("SWEET FB: display %s\n") in SweetFramebuffer.cpp kSweetFBRequestPowerChange:
    # VA 0xfffffe0007a69c44 (File 0xa65c44)
    # Replace 'b #0xfffffe0007a69d48' (0x14000041) with 'b #0xfffffe0007a69d4c' (0x14000042)
    struct.pack_into("<I", kernel_data, 0x00a65c44, 0x14000042)
    print("    Applied SweetFramebuffer display power IOLog bypass patch.")

    with open(KERNEL_TARGET, "wb") as f:
        f.write(kernel_data)
    print(f"    Saved patched kernel: {KERNEL_TARGET}")

    print("[3/4] Updating CMDLINE in build_sc54a_boot.py...")
    boot_script = os.path.join(TOOLS_DIR, "build_sc54a_boot.py")
    boot_code = open(boot_script, "r", encoding="utf-8").read()
    new_cmdline = (
        'CMDLINE = (\n'
        '    "debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress "\n'
        '    "rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 "\n'
        '    "ufsrw=1 rootrw=1 launchd_unsecure_cache=1 "\n'
        '    "sweetfb_noconsole=1 sweetfb_hz=30 sweetkeys=1 sweettouch=1 sweettouchdelay=2 sweetbattery=0 sweetusb=2"\n'
        ')'
    )
    import re
    boot_code = re.sub(r'CMDLINE = \([^)]+\)', new_cmdline, boot_code)
    open(boot_script, "w", encoding="utf-8").write(boot_code)

    print("[4/4] Executing full packaging build...")
    subprocess.run([sys.executable, boot_script], check=True)
    print("\n>>> ALL BUILD AND PACKAGING STEPS COMPLETED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    main()
