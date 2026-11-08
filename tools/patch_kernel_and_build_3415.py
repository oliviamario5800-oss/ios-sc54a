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
#define K_SWEET_HID_TOUCH            0xfffffe0007a6d158UL

#define SE_STATUS       0x040u
#define SE_MCLK         0x048u
#define SE_FW           0x068u
#define SE_CLK_SEL      0x07cu
#define SE_DMA          0x258u
#define SE_TX_LEN       0x26cu
#define SE_RX_LEN       0x270u
#define SE_SCL          0x278u
#define SE_CMD          0x600u
#define SE_CTRL         0x604u
#define SE_IRQ          0x610u
#define SE_IRQ_EN       0x614u
#define SE_IRQ_CLR      0x618u
#define SE_TX           0x700u
#define SE_RX           0x780u
#define SE_TX_COUNT     0x800u
#define SE_RX_COUNT     0x804u
#define SE_HW_TX        0xe24u
#define SE_HW_RX        0xe28u
#define SE_DONE         (1u << 0)
#define SE_NACK         (1u << 10)
#define SE_ERRORS       (0x0eu | (1u << 9) | (1u << 10) | (1u << 12) | \
                         (1u << 13) | (3u << 24) | (3u << 28))
#define SE_EVENTS       (SE_DONE | SE_ERRORS | (1u << 5) | (1u << 26) | \
                         (1u << 27) | (1u << 30))

#define TOUCH_MAX 10

static int touch_sleep_chan;
static uint32_t active_mask;
static int last_x[TOUCH_MAX];
static int last_y[TOUCH_MAX];
static int debug_touch = 1;

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

static int spmi_find_apid(vm_offset_t core, uint16_t ppid) {
    uint32_t count = mmio_rd(core, 4) & 0x7ffu;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t map = mmio_rd(core, 0x900u + 4u * i);
        if (map && (((map >> 8) & 0xfffu) == ppid)) {
            return (int)i;
        }
    }
    return -1;
}

static int spmi_write(vm_offset_t chnl, uint8_t addr, uint8_t val) {
    mmio_wr(chnl, 0x10u, (uint32_t)val);
    mmio_wr(chnl, 0x00u, (uint32_t)addr << 4);
    for (int i = 0; i < 2000; i++) {
        uint32_t st = mmio_rd(chnl, 0x08u);
        if (st & 1u) {
            return (st & 0x0eu) ? -2 : 0;
        }
        delay_us(1);
    }
    return -3;
}

static int spmi_read(vm_offset_t chnl, uint8_t addr, uint8_t *val) {
    mmio_wr(chnl, 0x00u, (1u << 27) | ((uint32_t)addr << 4));
    for (int i = 0; i < 2000; i++) {
        uint32_t st = mmio_rd(chnl, 0x08u);
        if (st & 1u) {
            if (st & 0x0eu) return -2;
            *val = (uint8_t)mmio_rd(chnl, 0x18u);
            return 0;
        }
        delay_us(1);
    }
    return -3;
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
    mmio_wr(se, SE_MCLK, (7u << 4) | 1u);
    mmio_wr(se, SE_SCL, (10u << 20) | (11u << 10) | 26u);
    mmio_wr(se, 0x254, 0);
    mmio_wr(se, 0x260, 0x0004380eu);
    mmio_wr(se, 0x264, 0x000c3e0eu);
    mmio_wr(se, 0x284, 0x0004380eu);
    mmio_wr(se, 0x288, 0x000c3e0eu);
    mmio_wr(se, 0x80c, 1);
    mmio_wr(se, 0x810, 0);
    mmio_wr(se, 0x814, 15);
    mmio_wr(se, SE_IRQ_EN, SE_EVENTS);
}

static int touch_msg(vm_offset_t se, uint8_t addr, const uint8_t *tx, unsigned nt, uint8_t *rx, unsigned nr, bool stretch) {
    if (nt > 3 || nr > 90 || (nt && nr) || (!nt && !nr)) return -1;
    for (int i = 0; i < 5000; i++) {
        if (!(mmio_rd(se, SE_STATUS) & 1u)) break;
        delay_us(1);
    }
    mmio_wr(se, SE_IRQ_CLR, 0xffffffffu);
    mmio_wr(se, nr ? SE_RX_LEN : SE_TX_LEN, nr ? nr : nt);
    mmio_wr(se, SE_CMD, ((nr ? 2u : 1u) << 27) | ((uint32_t)addr << 9) | (stretch ? 4u : 0u));
    unsigned sent = 0, got = 0;
    int err = -1;
    for (unsigned i = 0; i < 20000; i++) {
        uint32_t irq = mmio_rd(se, SE_IRQ);
        if (irq & SE_ERRORS) {
            err = (irq & SE_NACK) ? 1 : 2;
            for (unsigned j = 0; j < 5000 && !(irq & SE_DONE); j++) {
                delay_us(1);
                irq = mmio_rd(se, SE_IRQ);
            }
            if ((irq & SE_DONE) && !(mmio_rd(se, SE_STATUS) & 1u)) {
                mmio_wr(se, SE_IRQ_CLR, irq);
                return err;
            }
            break;
        }
        if (nt && !sent && (irq & (1u << 30))) {
            uint32_t word = 0;
            for (unsigned j = 0; j < nt; j++) word |= (uint32_t)tx[j] << (8u * j);
            mmio_wr(se, SE_TX, word);
            sent = nt;
        }
        uint32_t count = mmio_rd(se, SE_RX_COUNT) & 0x1ffffffu;
        while (count--) {
            uint32_t word = mmio_rd(se, SE_RX);
            for (unsigned j = 0; j < 4 && got < nr; j++) rx[got++] = (uint8_t)(word >> (8u * j));
        }
        if (irq & SE_DONE) {
            mmio_wr(se, SE_IRQ_CLR, irq);
            if (got == nr && sent == nt) return 0;
            return -1;
        }
        mmio_wr(se, SE_IRQ_CLR, irq & ((1u << 26) | (1u << 27) | (1u << 30)));
        delay_us(1);
    }
    mmio_wr(se, SE_CTRL, 2);
    for (int i = 0; i < 5000; i++) {
        if (mmio_rd(se, SE_IRQ) & (1u << 5)) break;
        delay_us(1);
    }
    mmio_wr(se, SE_IRQ_CLR, 0xffffffffu);
    return err;
}

static int mms_read_reg(vm_offset_t se, uint8_t addr, uint8_t reg, uint8_t *buf, unsigned len) {
    uint8_t ptr = reg;
    int err = touch_msg(se, addr, &ptr, 1, 0, 0, true);
    if (err) return err;
    return touch_msg(se, addr, 0, 0, buf, len, false);
}

static void mms_report(uint32_t mask, const int *x, const int *y) {
    for (unsigned id = 0; id < TOUCH_MAX; id++) {
        uint32_t bit = 1u << id;
        if (mask & bit) {
            if (!(active_mask & bit) || x[id] != last_x[id] || y[id] != last_y[id]) {
                if (debug_touch && !(active_mask & bit)) {
                    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: finger %u DOWN (%d,%d)\n", id, x[id], y[id]);
                }
                ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)((int)id, 1, x[id], y[id]);
            }
            last_x[id] = x[id];
            last_y[id] = y[id];
        } else if (active_mask & bit) {
            if (debug_touch) {
                ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: finger %u UP (%d,%d)\n", id, last_x[id], last_y[id]);
            }
            ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)((int)id, 0, last_x[id], last_y[id]);
        }
    }
    active_mask = mask;
}

static int mms_poll(vm_offset_t se) {
    uint8_t buf[60];
    int err = mms_read_reg(se, 0x48, 0x10, buf, sizeof(buf));
    if (err) return err;
    uint32_t mask = 0;
    int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};
    for (int i = 0; i < TOUCH_MAX; i++) {
        const uint8_t *p = buf + i * 6;
        int id = (p[0] & 0x0f) - 1;
        if (id < 0 || id >= TOUCH_MAX) break;
        bool down = (p[0] & 0x80) != 0;
        int raw_x = p[2] | ((p[1] & 0x0f) << 8);
        int raw_y = p[3] | (((p[1] >> 4) & 0x0f) << 8);
        if (raw_x > 4095) raw_x = 4095;
        if (raw_y > 4095) raw_y = 4095;
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
    return 0;
}

void sc54a_touch_thread(void *arg, int result) {
    (void)arg; (void)result;
    unsigned delay_s = 2;
    ((int (*)(const char *, void *, int))K_PE_PARSE_BOOT_ARGN)("sweettouchdelay", &delay_s, sizeof(delay_s));
    ((int (*)(const char *, void *, int))K_PE_PARSE_BOOT_ARGN)("sweettouchdebug", &debug_touch, sizeof(debug_touch));
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: SC-54A Melfas MSS100 starting in %u s\n", delay_s);
    touch_wait_ms(delay_s * 1000);

    /* 1. Map SPMI arbiter core to enable TSP LDO power via PM8150L GPIO 11 */
    vm_offset_t spmi_core = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x0c440000UL, 0x2000);
    if (spmi_core) {
        int apid = spmi_find_apid(spmi_core, 0x4ca); // PM8150L (SID 4) GPIO 11 (Periph 0xca)
        ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: PM8150L GPIO11 APID=%d\n", apid);
        if (apid >= 0) {
            vm_offset_t chnl_pa = 0x0c600000UL + (vm_offset_t)apid * 0x10000UL;
            vm_offset_t chnl = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(chnl_pa, 0x1000);
            if (chnl) {
                uint8_t mode_b = 0, en_b = 0;
                spmi_read(chnl, 0x40, &mode_b);
                spmi_read(chnl, 0x46, &en_b);
                /* Output High: push-pull (0x45=0x00), digital out high (0x40=0x12), enable (0x46=0x80) */
                int r1 = spmi_write(chnl, 0x45, 0x00);
                int r2 = spmi_write(chnl, 0x40, 0x12);
                int r3 = spmi_write(chnl, 0x46, 0x80);
                uint8_t mode_a = 0, en_a = 0;
                spmi_read(chnl, 0x40, &mode_a);
                spmi_read(chnl, 0x46, &en_a);
                ((int (*)(const char *, ...))K_PRINTF)(
                    "SWEET TOUCH: GPIO11 tsp_ldo_en: [before mode=%#x en=%#x] -> [after mode=%#x en=%#x] (res=%d,%d,%d)\n",
                    mode_b, en_b, mode_a, en_a, r1, r2, r3);
            }
        }
    }

    /* Wait 50 ms for TSP LDO to ramp to 3.0V and Melfas MSS100 POR reset */
    touch_wait_ms(50);

    vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00100000UL, 0x60000);
    vm_offset_t gpio = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x0f000000UL, 0x100000);
    vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00984000UL, 0x4000);

    if (!gcc || !gpio || !se) {
        ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MMIO map failed\n");
        return;
    }

    /* Enable QUPv3_1 SE7 clocks in GCC (M-AHB BIT20, S-AHB BIT21, SE1 BIT23) */
    mmio_wr(gcc, 0x52008, mmio_rd(gcc, 0x52008) | 0x00b00000u);

    /* Configure RCG for 19.2 MHz XO if not already running */
    uint32_t dfs = mmio_rd(gcc, 0x18148 + 0x14);
    if (!(dfs & 1u)) {
        uint32_t cfg = mmio_rd(gcc, 0x18148 + 4);
        cfg = (cfg & ~(0x371fu | (1u << 20))) | 1u;
        mmio_wr(gcc, 0x18148 + 4, cfg);
        mmio_wr(gcc, 0x18148, mmio_rd(gcc, 0x18148) | 1u);
        for (int i = 0; i < 5000; i++) {
            if (!(mmio_rd(gcc, 0x18148) & 1u)) break;
            delay_us(1);
        }
    }

    /* Enable USB 3.0 controller clocks in GCC */
    mmio_wr(gcc, 0x1a010, mmio_rd(gcc, 0x1a010) | 1u); // MASTER CBCR
    mmio_wr(gcc, 0x1a014, mmio_rd(gcc, 0x1a014) | 1u); // SLEEP CBCR
    mmio_wr(gcc, 0x1a018, mmio_rd(gcc, 0x1a018) | 1u); // MOCK UTMI CBCR

    /* Configure TLMM GPIOs for SC-54A: GPIO 6 & 7 = qup11 (I2C), GPIO 9 = IRQ */
    touch_gpio_cfg(gpio, 6, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 7, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 9, 0, 0, 0, false);
    touch_se_init(se);

    uint8_t probe_byte0 = 0, probe_byte10 = 0;
    int pe0 = mms_read_reg(se, 0x48, 0x00, &probe_byte0, 1);
    int pe10 = mms_read_reg(se, 0x48, 0x10, &probe_byte10, 1);
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MSS100 probe reg0=%d(0x%02x) reg10=%d(0x%02x)\n",
                                           pe0, probe_byte0, pe10, probe_byte10);

    /* Synthetic unlock swipe test after 3 seconds: bottom edge up to center */
    touch_wait_ms(3000);
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: injecting synthetic unlock swipe (edge up)\n");
    for (int step = 0; step <= 12; step++) {
        int cur_y = 2380 - (step * (2380 - 1500)) / 12;
        ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 1, 540, cur_y);
        touch_wait_ms(15);
    }
    ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 0, 540, 1500); // touch up
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: synthetic unlock gesture finished\n");

    unsigned err_count = 0;
    for (;;) {
        int err = mms_poll(se);
        if (err) {
            if ((err_count++ % 300) == 0) {
                ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: poll err=%d\n", err);
            }
        } else {
            err_count = 0;
        }
        touch_wait_ms(10);
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
        "-O2", "-ffreestanding", "-nostdlib", "-fno-pic", "-mstrict-align",
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

    # Maximum space is from 0xfffffe00074e921c to 0xfffffe00074ea994 = 6008 bytes
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
    # Offsets = VA - 0xfffffe0007004000
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

    with open(KERNEL_TARGET, "wb") as f:
        f.write(kernel_data)
    print(f"    Saved patched kernel: {KERNEL_TARGET}")

    print("[3/4] Updating CMDLINE in build_sc54a_boot.py...")
    boot_script = os.path.join(TOOLS_DIR, "build_sc54a_boot.py")
    boot_code = open(boot_script, "r", encoding="utf-8").read()
    # Ensure sweetfb_noconsole=0 (so user can see probe text) and sweettouchdelay=2
    new_cmdline = (
        'CMDLINE = (\n'
        '    "debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress "\n'
        '    "rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 "\n'
        '    "ufsrw=1 rootrw=1 launchd_unsecure_cache=1 "\n'
        '    "sweetfb_noconsole=0 sweetfb_hz=30 sweetkeys=1 sweettouch=1 sweettouchdelay=2 sweetbattery=0 sweetusb=2"\n'
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
