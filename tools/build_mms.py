import subprocess, struct, os

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TOOLS_DIR)
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
                ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)((int)id, 1, x[id], y[id]);
            }
            last_x[id] = x[id];
            last_y[id] = y[id];
        } else if (active_mask & bit) {
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
    unsigned delay_s = 5;
    ((int (*)(const char *, void *, int))K_PE_PARSE_BOOT_ARGN)("sweettouchdelay", &delay_s, sizeof(delay_s));
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: SC-54A Melfas MSS100 starting in %u s\n", delay_s);
    touch_wait_ms(delay_s * 1000);

    vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00100000UL, 0x60000);
    vm_offset_t gpio = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x0f000000UL, 0x100000);
    vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00984000UL, 0x4000);

    if (!gcc || !gpio || !se) {
        ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MMIO map failed\n");
        return;
    }

    mmio_wr(gcc, 0x52008, mmio_rd(gcc, 0x52008) | 0x00b00000u);
    touch_gpio_cfg(gpio, 6, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 7, 1, 0, 0, false);
    touch_gpio_cfg(gpio, 9, 0, 3, 3, false);
    touch_se_init(se);

    uint8_t probe_byte = 0;
    int pe = mms_read_reg(se, 0x48, 0x10, &probe_byte, 1);
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MSS100 probe err=%d byte=0x%02x\n", pe, probe_byte);

    unsigned err_count = 0;
    for (;;) {
        int err = mms_poll(se);
        if (err) {
            if ((err_count++ % 200) == 0) {
                ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: poll err=%d\n", err);
            }
        } else {
            err_count = 0;
        }
        touch_wait_ms(10);
    }
}
"""

src_path = os.path.join(TOOLS_DIR, "mms_driver.c")
obj_path = os.path.join(TOOLS_DIR, "mms_driver.o")
open(src_path, "w").write(C_SRC)

cmd = [
    ZIG, "cc", "-target", "aarch64-freestanding-none", "-mcpu=cortex_a55",
    "-O2", "-ffreestanding", "-nostdlib", "-fno-pic", "-mstrict-align",
    "-fno-stack-protector", "-c", src_path, "-o", obj_path
]
subprocess.run(cmd, check=True)
print("Successfully compiled mms_driver.o!")
