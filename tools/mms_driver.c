/*
 * SC-54A Melfas MSS100 polling driver payload.
 *
 * Hardware identified by stock DT: mms_ts@48 (I2C 7-bit address 0x48).
 * This is an ABI-specific XNU payload: K_* addresses and SweetHID entry
 * points below must be regenerated from the exact kernel being built.
 * It is not a loadable kext and must not be linked into another kernel as-is.
 *
 * Brings up PM8150L GPIO11 (TSP LDO), configures QUP11 TLMM pins, and polls
 * MSS100 reports. After a successful probe it sends one display-off/on key
 * pair and one synthetic bottom-edge swipe as diagnostics. PMIC writes are
 * specific to the SC-54A board described above.
 */
#include <stdint.h>
#include <stdbool.h>

typedef unsigned long vm_offset_t;
typedef unsigned long vm_size_t;

#define K_ML_IO_MAP                  0xfffffe0007490300UL
#define K_PE_PARSE_BOOT_ARGN         0xfffffe0007a77438UL
#define K_CLOCK_INTERVAL_TO_DEADLINE 0xfffffe000731f274UL
#define K_ASSERT_WAIT_DEADLINE       0xfffffe000735478cUL
#define K_THREAD_BLOCK               0xfffffe0007354e80UL
#define K_SWEET_HID_TOUCH            0xfffffe0007a6d158UL
#define K_SWEET_HID_KEY              0xfffffe0007a6d06cUL
#define K_TOUCH_MSG                  0xfffffe00074eaddcUL

/* SC-54A QUPv3 SE7 / GCC block, from the board-specific payload prototype. */
#define TOUCH_SE_PA      0x00984000UL
#define TOUCH_GCC_PA     0x00100000UL
#define TOUCH_RCG        0x18148u
#define TOUCH_I2C_ADDR   0x48u
#define TOUCH_TLMM_PA    0x0f000000UL
#define TOUCH_SPMI_CORE_PA 0x0c440000UL
#define TOUCH_SPMI_OBS_PA  0x0c600000UL
#define TOUCH_PMIC_GPIO11_PPID 0x4cau
#define TOUCH_MAX        10u
#define TOUCH_X_MAX      1080
#define TOUCH_Y_MAX      2400

#define SE_MCLK          0x048u
#define SE_CLK_SEL       0x07cu
#define SE_DMA           0x258u
#define SE_SCL           0x278u
#define SE_IRQ_CLR       0x618u
#define SE_IRQ_EN        0x614u

#define TOUCH_SLEEP_CHAN 0xfffffe00080528a0UL
#define ST_SE_BASE       (*(volatile vm_offset_t *)0xfffffe0008052818UL)
#define ST_DEAD          (*(volatile uint8_t *)0xfffffe0008052831UL)
#define ST_ACTIVE_MASK   (*(volatile uint32_t *)0xfffffe0008052840UL)
#define ST_LAST_X(id)    (((volatile int *)0xfffffe0008052844UL)[id])
#define ST_LAST_Y(id)    (((volatile int *)0xfffffe0008052870UL)[id])

static int touch_diag_done;

static inline uint32_t
mmio_rd(vm_offset_t base, uint32_t off)
{
	uint32_t v = *(volatile uint32_t *)(base + off);
	__asm__ volatile("dmb oshld" ::: "memory");
	return v;
}

static inline void
mmio_wr(vm_offset_t base, uint32_t off, uint32_t val)
{
	__asm__ volatile("dmb oshst" ::: "memory");
	*(volatile uint32_t *)(base + off) = val;
	__asm__ volatile("dsb osh" ::: "memory");
}

static void
touch_wait_ms(unsigned ms)
{
	uint64_t deadline;
	((void (*)(uint32_t, uint64_t, uint64_t *))K_CLOCK_INTERVAL_TO_DEADLINE)(
	    ms, 1000000ULL, &deadline);
	((void (*)(void *, int, uint64_t))K_ASSERT_WAIT_DEADLINE)(
	    (void *)TOUCH_SLEEP_CHAN, 0, deadline);
	((void (*)(void *))K_THREAD_BLOCK)(0);
}

static void
touch_se_init(vm_offset_t se)
{
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

static int
spmi_find_apid(vm_offset_t core, uint16_t ppid)
{
	uint32_t count = mmio_rd(core, 4) & 0x7ffu;
	if (count == 0 || count > 512) return -1;
	for (uint32_t i = 0; i < count; ++i) {
		uint32_t map = mmio_rd(core, 0x900u + 4u * i);
		if (map != 0 && (((map >> 8) & 0xfffu) == ppid)) return (int)i;
	}
	return -1;
}

static int
spmi_read(vm_offset_t ch, uint8_t addr, uint8_t *value)
{
	mmio_wr(ch, 0x00u, (1u << 27) | ((uint32_t)addr << 4));
	for (unsigned i = 0; i < 2000; ++i) {
		uint32_t status = mmio_rd(ch, 0x08u);
		if (status & 1u) {
			if (status & 0x0eu) return -2;
			*value = (uint8_t)mmio_rd(ch, 0x18u);
			return 0;
		}
	}
	return -3;
}

static int
spmi_write(vm_offset_t ch, uint8_t addr, uint8_t value)
{
	mmio_wr(ch, 0x10u, value);
	mmio_wr(ch, 0x00u, (uint32_t)addr << 4);
	for (unsigned i = 0; i < 2000; ++i) {
		uint32_t status = mmio_rd(ch, 0x08u);
		if (status & 1u) return (status & 0x0eu) ? -2 : 0;
	}
	return -3;
}

static int
touch_power_on(void)
{
	vm_offset_t core = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(
	    TOUCH_SPMI_CORE_PA, 0x2000);
	if (!core) return -1;
	int apid = spmi_find_apid(core, TOUCH_PMIC_GPIO11_PPID);
	if (apid < 0) return -2;
	vm_offset_t ch = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(
	    TOUCH_SPMI_OBS_PA + (vm_offset_t)apid * 0x10000UL, 0x1000);
	if (!ch) return -3;
	uint8_t old_mode = 0, old_enable = 0;
	(void)spmi_read(ch, 0x40, &old_mode);
	(void)spmi_read(ch, 0x46, &old_enable);
	/* Push-pull digital output, drive high, then enable PMIC GPIO11. */
	int r1 = spmi_write(ch, 0x45, 0x00);
	int r2 = spmi_write(ch, 0x40, 0x12);
	int r3 = spmi_write(ch, 0x46, 0x80);
	uint8_t new_mode = 0, new_enable = 0;
	int r4 = spmi_read(ch, 0x40, &new_mode);
	int r5 = spmi_read(ch, 0x46, &new_enable);
	(void)old_mode;
	(void)old_enable;
	return (r1 || r2 || r3 || r4 || r5 || new_mode != 0x12 ||
	    !(new_enable & 0x80u)) ? -4 : 0;
}

static void
touch_gpio_cfg(vm_offset_t base, unsigned pin, unsigned mux)
{
	uint32_t off = pin * 0x1000u;
	uint32_t cfg = mmio_rd(base, off) & ~0x3ffu;
	/* Function mux, no pull, minimum drive; retain input direction. */
	cfg |= (mux << 2);
	mmio_wr(base, off, cfg);
}

static int
mms_read_reg(uint8_t reg_hi, uint8_t reg_lo, uint8_t *buf, unsigned len)
{
	uint8_t cmd[2] = { reg_hi, reg_lo };
	ST_DEAD = 0;
	int err = ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *,
	    unsigned, bool))K_TOUCH_MSG)(TOUCH_I2C_ADDR, cmd, sizeof(cmd),
	    0, 0, true);
	if (err != 0) return err;
	ST_DEAD = 0;
	return ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *,
	    unsigned, bool))K_TOUCH_MSG)(TOUCH_I2C_ADDR, 0, 0, buf, len, false);
}

static int
mms_write_reg(uint8_t reg_hi, uint8_t reg_lo, uint8_t value)
{
	uint8_t cmd[3] = { reg_hi, reg_lo, value };
	ST_DEAD = 0;
	return ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *,
	    unsigned, bool))K_TOUCH_MSG)(TOUCH_I2C_ADDR, cmd, sizeof(cmd),
	    0, 0, false);
}

static void
mms_report(uint32_t mask, const int *x, const int *y)
{
	uint32_t old_mask = ST_ACTIVE_MASK;
	for (unsigned id = 0; id < TOUCH_MAX; ++id) {
		uint32_t bit = 1u << id;
		if (mask & bit) {
			if (!(old_mask & bit) || x[id] != ST_LAST_X(id) ||
			    y[id] != ST_LAST_Y(id)) {
				((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(
				    (int)id, 1, x[id], y[id]);
			}
			ST_LAST_X(id) = x[id];
			ST_LAST_Y(id) = y[id];
		} else if (old_mask & bit) {
			((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(
			    (int)id, 0, ST_LAST_X(id), ST_LAST_Y(id));
		}
	}
	ST_ACTIVE_MASK = mask;
}

static int
mms_poll(void)
{
	uint8_t pkt_info = 0;
	int err = mms_read_reg(0x02, 0x10, &pkt_info, 1);
	if (err != 0) return err;

	unsigned size = pkt_info & 0x7fu;
	if (size == 0) {
		if (ST_ACTIVE_MASK != 0) {
			int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};
			mms_report(0, x, y);
		}
		return 0;
	}
	if (size > 80) size = 80;
	uint8_t buf[80];
	err = mms_read_reg(0x02, 0x11, buf, size);
	if (err != 0) return err;

	uint32_t mask = 0;
	int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};
	for (unsigned off = 0; off + 4 <= size; off += 16) {
		const uint8_t *p = buf + off;
		int id = (int)(p[0] & 0x0fu) - 1;
		if (id < 0 || id >= (int)TOUCH_MAX || !(p[0] & 0x80u)) continue;
		int raw_x = ((int)(p[1] & 0x0fu) << 8) | p[2];
		int raw_y = ((int)(p[1] >> 4) << 8) | p[3];
		int px = (raw_x * TOUCH_X_MAX) / 4095;
		int py = (raw_y * TOUCH_Y_MAX) / 4095;
		if (px >= TOUCH_X_MAX) px = TOUCH_X_MAX - 1;
		if (py >= TOUCH_Y_MAX) py = TOUCH_Y_MAX - 1;
		mask |= 1u << id;
		x[id] = px;
		y[id] = py;
	}
	mms_report(mask, x, y);
	return 0;
}

static void
touch_power_button_pulse(void)
{
	((void (*)(int, int))K_SWEET_HID_KEY)(0, 1);
	touch_wait_ms(80);
	((void (*)(int, int))K_SWEET_HID_KEY)(0, 0);
}

static void
touch_one_shot_feedback(void)
{
	if (touch_diag_done) return;
	touch_diag_done = 1;
	/* One display off/on cycle, only after the MSS100 responds over I2C. */
	touch_power_button_pulse();
	touch_wait_ms(1500);
	touch_power_button_pulse();
	touch_wait_ms(500);
	/* One swipe starts below the iOS edge threshold (y >= 2351). */
	for (int step = 0; step <= 16; ++step) {
		int y = 2380 - (step * (2380 - 1200)) / 16;
		((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 1, 540, y);
		touch_wait_ms(15);
	}
	((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 0, 540, 1200);
}

void
sc54a_touch_thread(void *arg, int result)
{
	(void)arg;
	(void)result;
	unsigned delay_s = 5;
	(void)((int (*)(const char *, void *, int))K_PE_PARSE_BOOT_ARGN)(
	    "sweettouchdelay", &delay_s, sizeof(delay_s));
	touch_wait_ms(delay_s * 1000);
	if (touch_power_on() != 0) return;
	touch_wait_ms(90); /* TSP LDO stabilization and controller POR. */
	vm_offset_t gpio = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(
	    TOUCH_TLMM_PA, 0x100000);
	if (!gpio) return;
	touch_gpio_cfg(gpio, 6, 1); /* GPIO6: QUP11 SCL */
	touch_gpio_cfg(gpio, 7, 1); /* GPIO7: QUP11 SDA */
	touch_gpio_cfg(gpio, 9, 0); /* GPIO9: TSP interrupt input */

	vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(
	    TOUCH_GCC_PA, 0x60000);
	vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(
	    TOUCH_SE_PA, 0x4000);
	if (!gcc || !se) return;

	ST_SE_BASE = se;
	ST_DEAD = 0;
	ST_ACTIVE_MASK = 0;
	uint32_t mask = (0x0fu << 18) | (1u << 23);
	mmio_wr(gcc, 0x52008, mmio_rd(gcc, 0x52008) | mask);
	mmio_wr(gcc, 0x5200c, mmio_rd(gcc, 0x5200c) | mask);
	uint32_t dfs = mmio_rd(gcc, TOUCH_RCG + 0x14u);
	if (!(dfs & 1u)) {
		uint32_t cfg = mmio_rd(gcc, TOUCH_RCG + 4u);
		cfg = (cfg & ~(0x371fu | (1u << 20))) | 1u;
		mmio_wr(gcc, TOUCH_RCG + 4u, cfg);
		mmio_wr(gcc, TOUCH_RCG, mmio_rd(gcc, TOUCH_RCG) | 1u);
		for (unsigned i = 0; i < 5000; ++i) {
			if (!(mmio_rd(gcc, TOUCH_RCG) & 1u)) break;
			touch_wait_ms(1);
		}
	}
	static const uint32_t branches[] = {0x18004, 0x18008, 0x1800c,
	    0x18010, 0x18144};
	for (unsigned i = 0; i < sizeof(branches) / sizeof(branches[0]); ++i) {
		for (unsigned n = 0; n < 1000; ++n) {
			if (!(mmio_rd(gcc, branches[i]) & (1u << 31))) break;
			touch_wait_ms(1);
		}
	}

	touch_se_init(se);
	touch_wait_ms(50);
	uint8_t ready = 0;
	int probe = mms_read_reg(0x06, 0x00, &ready, 1);
	if (probe != 0) {
		uint8_t packet = 0;
		probe = mms_read_reg(0x02, 0x10, &packet, 1);
	}
	if (probe == 0) {
		/* MSS100 ACTIVE mode and ESD-alert policy used by the stock path. */
		(void)mms_write_reg(0x06, 0x13, 0x00);
		touch_wait_ms(20);
		(void)mms_write_reg(0x06, 0x18, 0x01);
		touch_wait_ms(10);
		touch_one_shot_feedback();
	}
	for (;;) {
		int err = mms_poll();
		touch_wait_ms(err ? 20 : 10);
	}
}
