import subprocess, struct, os, sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ZIG = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"

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
#define K_SWEET_HID_KEY              0xfffffe0007a6d06cUL

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
    if (nt > 4 || nr > 100 || (nt && nr) || (!nt && !nr)) return -1;
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
        if (nt && (sent < nt) && (irq & (1u << 30))) {
            uint32_t word = 0;
            for (unsigned j = 0; j < 4 && (sent + j) < nt; j++) {
                word |= (uint32_t)tx[sent + j] << (8u * j);
            }
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

static int mms_read_reg2(vm_offset_t se, uint8_t addr, uint8_t r0, uint8_t r1, uint8_t *buf, unsigned len) {
    uint8_t cmd[2] = { r0, r1 };
    int err = touch_msg(se, addr, cmd, 2, 0, 0, true);
    if (err) return err;
    return touch_msg(se, addr, 0, 0, buf, len, false);
}

static int mms_write_reg2(vm_offset_t se, uint8_t addr, uint8_t r0, uint8_t r1, uint8_t val) {
    uint8_t cmd[3] = { r0, r1, val };
    return touch_msg(se, addr, cmd, 3, 0, 0, false);
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
    uint8_t pkt_info = 0;
    int err = mms_read_reg2(se, 0x48, 0x02, 0x10, &pkt_info, 1);
    if (err) return err;

    uint8_t count = pkt_info & 0x7fu;
    if (count == 0 || (pkt_info & 0x80u)) {
        if (active_mask != 0) {
            mms_report(0, last_x, last_y);
        }
        return 0;
    }

    if (count > TOUCH_MAX) count = TOUCH_MAX;
    uint8_t buf[100];
    unsigned total_len = (unsigned)count * 10u;
    err = mms_read_reg2(se, 0x48, 0x02, 0x11, buf, total_len);
    if (err) return err;

    uint32_t mask = 0;
    int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};

    for (unsigned i = 0; i < count; i++) {
        const uint8_t *p = buf + (i * 10u);
        if (!(p[0] & 0x40u)) continue; // not screen touch event
        int id = (p[0] & 0x0fu) - 1;
        if (id < 0 || id >= TOUCH_MAX) continue;
        bool down = (p[0] & 0x80u) != 0;

        int raw_x = ((int)(p[1] & 0x0fu) << 8) | (int)p[2];
        int raw_y = ((int)(p[1] >> 4) << 8) | (int)p[3];
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

static void do_unlock_swipe(void) {
    /* Home key click (key 3) to wake screen / prompt unlock */
    ((void (*)(int, int))K_SWEET_HID_KEY)(3, 1);
    touch_wait_ms(40);
    ((void (*)(int, int))K_SWEET_HID_KEY)(3, 0);
    touch_wait_ms(60);

    /* Smooth swipe up from bottom edge: y = 2390 down to 1200 in 15 steps */
    for (int step = 0; step <= 15; step++) {
        int cur_y = 2390 - (step * (2390 - 1200)) / 15;
        ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 1, 540, cur_y);
        touch_wait_ms(15);
    }
    ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 0, 540, 1200);
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

    /* Wait 90 ms for TSP LDO to stabilize and Melfas MSS100 POR reset */
    touch_wait_ms(90);

    vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00100000UL, 0x60000);
    vm_offset_t gpio = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x0f000000UL, 0x100000);
    vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00984000UL, 0x4000);

    if (!gcc || !gpio || !se) {
        ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MMIO map failed\n");
        return;
    }

    /* Enable QUPv3_1 SE7 AHB & Core clocks in GCC (M-AHB bit 20, S-AHB bit 21, SE1 bit 23) */
    mmio_wr(gcc, 0x52008, mmio_rd(gcc, 0x52008) | 0x00b00000u);

    /* Configure RCG for 19.2 MHz XO if not running */
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

    /* CRITICAL FIX: Enable QUPv3_WRAP1_S1 branch CBCR clock at 0x18158 */
    mmio_wr(gcc, 0x18158, mmio_rd(gcc, 0x18158) | 1u);
    for (int i = 0; i < 5000; i++) {
        if (!(mmio_rd(gcc, 0x18158) & (1u << 31))) break; // bit 31 CLK_OFF == 0
        delay_us(1);
    }
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: QUPv3 SE7 CBCR 0x18158 = %#x\n", mmio_rd(gcc, 0x18158));

    /* Configure TLMM GPIOs: GPIO 6 & 7 = qup11 (I2C), GPIO 9 = IRQ */
    touch_gpio_cfg(gpio, 6, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 7, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 9, 0, 0, 0, false);
    touch_se_init(se);

    /* Melfas MSS100 Init: Query ready status */
    uint8_t boot_st = 0;
    int pe = -1;
    for (int retry = 0; retry < 20; retry++) {
        pe = mms_read_reg2(se, 0x48, 0x06, 0x00, &boot_st, 1);
        if (pe == 0 && (boot_st == 0xa0 || boot_st == 0x05)) break;
        touch_wait_ms(10);
    }
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MSS100 ready_status=%d(0x%02x)\n", pe, boot_st);

    /* Switch to ACTIVE power mode: { 0x06, 0x13, 0x00 } */
    int pr1 = mms_write_reg2(se, 0x48, 0x06, 0x13, 0x00);
    touch_wait_ms(20);

    /* Disable ESD alert: { 0x06, 0x18, 0x01 } */
    int pr2 = mms_write_reg2(se, 0x48, 0x06, 0x18, 0x01);
    touch_wait_ms(10);

    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MSS100 init cmds res: pwr=%d esd=%d\n", pr1, pr2);

    unsigned err_count = 0;
    unsigned elapsed_ms = 0;
    unsigned last_unlock_ms = 0;

    for (;;) {
        int err = mms_poll(se);
        if (err) {
            if ((err_count++ % 300) == 0) {
                ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: poll err=%d\n", err);
            }
        } else {
            err_count = 0;
        }

        /* Periodic unlock trigger: every 6 seconds after t >= 20 seconds */
        elapsed_ms += 10;
        if (elapsed_ms >= 20000 && (elapsed_ms - last_unlock_ms) >= 6000) {
            last_unlock_ms = elapsed_ms;
            ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: firing unlock trigger at t=%u ms\n", elapsed_ms);
            do_unlock_swipe();
        }

        touch_wait_ms(10);
    }
}
"""

def main():
    src_c = os.path.join(TOOLS_DIR, "test_driver.c")
    obj_o = os.path.join(TOOLS_DIR, "test_driver.o")
    elf_out = os.path.join(TOOLS_DIR, "test_driver.elf")
    ld_script = os.path.join(TOOLS_DIR, "test_driver.ld")

    with open(src_c, "w") as f:
        f.write(C_SRC)

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

    print(f"Compiled .text size: {len(text_bytes)} bytes (limit: 6008 bytes)")
    assert len(text_bytes) + 4 <= 6008, "Too large!"
    print("SUCCESS!")

if __name__ == "__main__":
    main()
