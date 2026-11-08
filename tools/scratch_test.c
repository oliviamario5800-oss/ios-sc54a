
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
    int err = ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_GENI_I2C_XFER)(addr, cmd, 2, 0, 0, true);
    if (err) return err;
    return ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_GENI_I2C_XFER)(addr, 0, 0, buf, len, false);
}

static int mms_write_reg2(uint8_t addr, uint8_t r0, uint8_t r1, uint8_t val) {
    uint8_t cmd[3] = { r0, r1, val };
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
    unsigned delay_s = 2;
    ((int (*)(const char *, void *, int))K_PE_PARSE_BOOT_ARGN)("sweettouchdelay", &delay_s, sizeof(delay_s));
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
                /* Output High: push-pull (0x45=0x00), digital out high (0x40=0x12), enable (0x46=0x80) */
                spmi_write(chnl, 0x45, 0x00);
                spmi_write(chnl, 0x40, 0x12);
                spmi_write(chnl, 0x46, 0x80);
                ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: TSP LDO enabled via PM8150L GPIO11\n");
            }
        }
    }

    /* Wait 90 ms for TSP LDO (3.0V) and MSS100 POR (matches stock kernel msleep(90)) */
    touch_wait_ms(90);

    vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00100000UL, 0x60000);
    vm_offset_t gpio = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x0f000000UL, 0x100000);
    vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(0x00984000UL, 0x4000);

    if (!gcc || !gpio || !se) {
        ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MMIO map failed\n");
        return;
    }

    /* Set global pointers for native GENI I2C driver (0xfffffe00074eaddc) */
    *(volatile vm_offset_t *)0xfffffe0008052818UL = se;
    *(volatile vm_offset_t *)0xfffffe0008052820UL = gcc;

    /* Enable QUPv3_1 wrapper clocks in GCC vote register 0x5200c (mask 0x00bc0000) */
    mmio_wr(gcc, 0x5200c, mmio_rd(gcc, 0x5200c) | 0x00bc0000u);

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

    /* Enable branch CBCRs: WRAP1 M_AHB, S_AHB, CORE, CORE_2X, SE1 */
    mmio_wr(gcc, 0x18004, mmio_rd(gcc, 0x18004) | 1u);
    mmio_wr(gcc, 0x18008, mmio_rd(gcc, 0x18008) | 1u);
    mmio_wr(gcc, 0x1800c, mmio_rd(gcc, 0x1800c) | 1u);
    mmio_wr(gcc, 0x18010, mmio_rd(gcc, 0x18010) | 1u);
    mmio_wr(gcc, 0x18144, mmio_rd(gcc, 0x18144) | 1u);

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
    int pe = -1;
    for (int retry = 0; retry < 10; retry++) {
        pe = mms_read_reg2(0x48, 0x06, 0x00, &boot_st, 1);
        if (pe == 0 && (boot_st == 0xa0 || boot_st == 0x05)) break;
        touch_wait_ms(10);
    }
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MSS100 ready_status=%d(0x%02x)\n", pe, boot_st);

    /* Switch to ACTIVE power mode: { 0x06, 0x13, 0x00 } */
    int pr1 = mms_write_reg2(0x48, 0x06, 0x13, 0x00);
    touch_wait_ms(20);

    /* Disable ESD alert: { 0x06, 0x18, 0x01 } */
    int pr2 = mms_write_reg2(0x48, 0x06, 0x18, 0x01);
    touch_wait_ms(10);
    ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: MSS100 init cmds res: pwr=%d esd=%d\n", pr1, pr2);

    unsigned err_count = 0;
    for (;;) {
        int err = mms_poll();
        if (err) {
            if ((err_count++ % 300) == 0) {
                ((int (*)(const char *, ...))K_PRINTF)("SWEET TOUCH: poll err=%d\n", err);
            }
            touch_wait_ms(20);
        } else {
            err_count = 0;
            touch_wait_ms(10);
        }
    }
}
