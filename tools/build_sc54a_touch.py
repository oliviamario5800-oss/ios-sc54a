import subprocess, struct, os, sys, shutil

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TOOLS_DIR)
ZIG = r"C:\Users\user\Tools\zig-x86_64-windows-0.17.0\zig.exe"
KERNEL_GOOD = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a.GOOD")
KERNEL_TARGET = os.path.join(PROJ_DIR, "xnu", "kernel.development.sc54a")

C_SRC = r"""
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

#define TOUCH_SE_PA     0x00984000UL
#define TOUCH_GCC_PA    0x00100000UL
#define TOUCH_GCC_VOTE  0x52008u
#define TOUCH_RCG       0x18148u

#define SE_STATUS       0x040u
#define SE_MCLK         0x048u
#define SE_CLK_SEL      0x07cu
#define SE_DMA          0x258u
#define SE_SCL          0x278u
#define SE_IRQ_CLR      0x618u
#define SE_IRQ_EN       0x614u

#define TOUCH_SLEEP_CHAN 0xfffffe00080528a0UL
#define ST_SE_BASE       (*(volatile vm_offset_t *)0xfffffe0008052818UL)
#define ST_DEAD          (*(volatile uint8_t *)0xfffffe0008052831UL)
#define ST_ACTIVE_MASK   (*(volatile uint32_t *)0xfffffe0008052840UL)
#define ST_LAST_X(id)    (((volatile int *)0xfffffe0008052844UL)[id])
#define ST_LAST_Y(id)    (((volatile int *)0xfffffe0008052870UL)[id])

#define G_SWEET_TOUCH    (*(void * volatile *)0xfffffe0008072040UL)
#define G_SWEET_BUTTONS  (*(void * volatile *)0xfffffe0008072038UL)

#define TOUCH_MAX 10

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

static void touch_wait_ms(unsigned ms) {
    uint64_t deadline;
    ((void (*)(uint32_t, uint64_t, uint64_t *))K_CLOCK_INTERVAL_TO_DEADLINE)(ms, 1000000ULL, &deadline);
    ((void (*)(void *, int, uint64_t))K_ASSERT_WAIT_DEADLINE)((void *)TOUCH_SLEEP_CHAN, 0, deadline);
    ((void (*)(void *))K_THREAD_BLOCK)(0);
}

static void power_button_pulse(void) {
    ((void (*)(int, int))K_SWEET_HID_KEY)(0, 1);
    touch_wait_ms(80);
    ((void (*)(int, int))K_SWEET_HID_KEY)(0, 0);
}

static void synthetic_swipe_up(void) {
    if (!G_SWEET_TOUCH) return;
    for (int y = 2200; y >= 800; y -= 40) {
        ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 1, 540, y);
        touch_wait_ms(16);
    }
    ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)(0, 0, 540, 800);
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

static int mms_read_reg2(uint8_t r0, uint8_t r1, uint8_t *buf, unsigned len) {
    uint8_t cmd[2] = { r0, r1 };
    ST_DEAD = 0;
    /* Step 1: Write register address with stretch=true (repeated START) */
    int err = ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_TOUCH_MSG)(0x48, cmd, 2, 0, 0, true);
    if (err) return err;
    ST_DEAD = 0;
    /* Step 2: Read data without stretch (false = STOP) */
    return ((int (*)(uint8_t, const uint8_t *, unsigned, uint8_t *, unsigned, bool))K_TOUCH_MSG)(0x48, 0, 0, buf, len, false);
}

static void mms_report(uint32_t mask, const int *x, const int *y) {
    uint32_t old_mask = ST_ACTIVE_MASK;
    for (unsigned id = 0; id < TOUCH_MAX; id++) {
        uint32_t bit = 1u << id;
        if (mask & bit) {
            if (!(old_mask & bit) || x[id] != ST_LAST_X(id) || y[id] != ST_LAST_Y(id)) {
                ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)((int)id, 1, x[id], y[id]);
            }
            ST_LAST_X(id) = x[id];
            ST_LAST_Y(id) = y[id];
        } else if (old_mask & bit) {
            ((void (*)(int, int, int, int))K_SWEET_HID_TOUCH)((int)id, 0, ST_LAST_X(id), ST_LAST_Y(id));
        }
    }
    ST_ACTIVE_MASK = mask;
}

static int mms_poll(void) {
    uint8_t pkt_info = 0;
    int err = mms_read_reg2(0x02, 0x10, &pkt_info, 1);
    if (err) return err;

    uint8_t size = pkt_info & 0x7fu;
    if (size == 0) {
        if (ST_ACTIVE_MASK != 0) {
            int dummy_x[TOUCH_MAX] = {0}, dummy_y[TOUCH_MAX] = {0};
            mms_report(0, dummy_x, dummy_y);
        }
        return 0;
    }

    if (size > 80) size = 80;
    uint8_t buf[80];
    err = mms_read_reg2(0x02, 0x11, buf, size);
    if (err) return err;

    uint32_t mask = 0;
    int x[TOUCH_MAX] = {0}, y[TOUCH_MAX] = {0};

    for (unsigned off = 0; off + 4 <= size; off += 16) {
        const uint8_t *p = buf + off;
        int id = (p[0] & 0x0fu) - 1;
        if (id < 0 || id >= TOUCH_MAX) continue;
        bool down = (p[0] & 0x80u) != 0;

        int raw_x = ((int)(p[1] & 0x0fu) << 8) | (int)p[2];
        int raw_y = (((int)(p[1] >> 4) & 0x0fu) << 8) | (int)p[3];

        int px = (raw_x * 1080) / 4095;
        int py = (raw_y * 2400) / 4095;
        if (px < 0) px = 0; else if (px > 1079) px = 1079;
        if (py < 0) py = 0; else if (py > 2399) py = 2399;

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
    touch_wait_ms(delay_s * 1000);

    /* Wait up to 10s for SweetHIDTouch to be registered */
    for (int i = 0; i < 20 && !G_SWEET_TOUCH; i++) {
        touch_wait_ms(500);
    }

    /* VISUAL TEST 1: Synthetic swipe-up on lock screen */
    /* This tests SweetHIDTouch in iOS userspace: lock screen slides up to Passcode! */
    synthetic_swipe_up();
    touch_wait_ms(1500);

    /* Map GCC and QUPv3_1 SE1 (SE7) */
    vm_offset_t gcc = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(TOUCH_GCC_PA, 0x60000);
    vm_offset_t se = ((vm_offset_t (*)(vm_offset_t, vm_size_t))K_ML_IO_MAP)(TOUCH_SE_PA, 0x4000);

    if (!gcc || !se) {
        for (;;) {
            touch_wait_ms(2000);
        }
    }

    ST_SE_BASE = se;
    ST_DEAD = 0;
    ST_ACTIVE_MASK = 0;

    /* Vote for QUPv3_1 wrapper clocks in GCC vote registers (0x52008 & 0x5200c, mask 0x00bc0000) */
    uint32_t mask = (0x0fu << 18) | (1u << 23);
    mmio_wr(gcc, 0x52008, mmio_rd(gcc, 0x52008) | mask);
    mmio_wr(gcc, 0x5200c, mmio_rd(gcc, 0x5200c) | mask);

    /* Configure RCG for 19.2 MHz XO on Wrapper 1 SE1 (0x18148) if DFS not enabled */
    uint32_t dfs = mmio_rd(gcc, TOUCH_RCG + 0x14u);
    if (!(dfs & 1u)) {
        uint32_t cfg = mmio_rd(gcc, TOUCH_RCG + 4u);
        cfg = (cfg & ~(0x371fu | (1u << 20))) | 1u;
        mmio_wr(gcc, TOUCH_RCG + 4u, cfg);
        mmio_wr(gcc, TOUCH_RCG, mmio_rd(gcc, TOUCH_RCG) | 1u);
        for (int i = 0; i < 5000; i++) {
            if (!(mmio_rd(gcc, TOUCH_RCG) & 1u)) break;
            touch_wait_ms(1);
        }
    }

    /* Wait for GCC branch clocks to unhalt */
    const uint32_t branches[] = {0x18004, 0x18008, 0x1800c, 0x18010, 0x18144};
    for (unsigned i = 0; i < 5; ++i) {
        for (int k = 0; k < 1000; k++) {
            if (!(mmio_rd(gcc, branches[i]) & (1u << 31))) break;
            touch_wait_ms(1);
        }
    }

    /* Initialize SE7 FIFO */
    touch_se_init(se);
    touch_wait_ms(50);

    /* VISUAL TEST 2: Probe Melfas MSS100 I2C */
    uint8_t pkt_info = 0;
    int err = mms_read_reg2(0x06, 0x00, &pkt_info, 1);
    if (err) {
        err = mms_read_reg2(0x02, 0x10, &pkt_info, 1);
    }

    if (err == 0 && G_SWEET_BUTTONS) {
        /* I2C PROBE SUCCESS: Pulse Power button once! */
        /* Screen sleeps for 1.5s then wakes back up to confirm Melfas MSS100 I2C is alive! */
        power_button_pulse();
        touch_wait_ms(1500);
        power_button_pulse();
    }

    /* Main Polling Loop for Physical Touch */
    for (;;) {
        ST_DEAD = 0;
        int poll_err = mms_poll();
        if (poll_err) {
            touch_wait_ms(20);
        } else {
            touch_wait_ms(8);
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

    with open(ld_script, "w") as f:
        f.write("""
SECTIONS {
    . = 0xfffffe00074e9220;
    .text : {
        *(.text*)
        *(.rodata*)
    }
    /DISCARD/ : {
        *(.data*)
        *(.bss*)
        *(COMMON)
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

    assert text_bytes is not None and text_addr == 0xfffffe00074e9220
    print(f"    Compiled .text payload: {len(text_bytes)} bytes at {text_addr:#x}")

    total_driver_size = 4 + len(text_bytes)
    assert total_driver_size <= 6008, f"Driver too large: {total_driver_size} > 6008"

    print("[2/4] Patching kernel.development.sc54a (FROM CLEAN .GOOD BASELINE)...")
    kernel_data = bytearray(open(KERNEL_GOOD, "rb").read())

    # Patch ONLY _touch_thread:
    # At 0xfffffe00074e921c (file offset 0x4e521c):
    # 'b 0xfffffe00074e9220' (ARM64: 0x14000001)
    touch_entry_file_off = 0x004e521c
    struct.pack_into("<I", kernel_data, touch_entry_file_off, 0x14000001)
    kernel_data[touch_entry_file_off + 4 : touch_entry_file_off + 4 + len(text_bytes)] = text_bytes
    print(f"    Injected touch driver at offset {touch_entry_file_off:#x} ({total_driver_size} bytes)")

    with open(KERNEL_TARGET, "wb") as f:
        f.write(kernel_data)
    print(f"    Saved patched kernel: {KERNEL_TARGET}")

    print("[3/4] Updating CMDLINE in build_sc54a_boot.py...")
    boot_script = os.path.join(TOOLS_DIR, "build_sc54a_boot.py")
    boot_code = open(boot_script, "r", encoding="utf-8").read()
    new_cmdline = (
        'CMDLINE = (\n'
        '    "debug=0xc serial=3 cpus=8 keepsyms=1 -enable_kprintf_spam -noprogress "\n'
        '    "rd=ufsroot0 unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin "\n'
        '    "ufsrw=1 rootrw=1 launchd_unsecure_cache=1 wdt=-1 "\n'
        '    "sweetfb_noconsole=1 sweetfb_hz=30 sweetkeys=1 sweettouch=1 sweettouchdelay=5 sweetbattery=0 sweetusb=0"\n'
        ')'
    )
    import re
    boot_code = re.sub(r'CMDLINE = \([^)]+\)', new_cmdline, boot_code)
    open(boot_script, "w", encoding="utf-8").write(boot_code)

    print("[4/4] Executing build...")
    subprocess.run([sys.executable, boot_script], check=True)
    print("\n>>> MELFAS MSS100 TOUCH BUILD COMPLETED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    main()
