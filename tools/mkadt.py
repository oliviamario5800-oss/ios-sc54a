#!/usr/bin/env python3
"""Build the minimal Apple Device Tree that the SC-54A xnu needs to boot.

Target device: Samsung Galaxy A51 5G (NTT Docomo SC-54A / au SCG07 / a51xq)
Platform: Qualcomm Snapdragon 765G (SM7250 / "lito")

Usage:  mkadt.py OUT.adt [--dump] [--qemu] [--highmem]
"""
import hashlib, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Kernel-managed RAM on SC-54A (6 GB SM7250 upper DDR bank <0x1_00000000 0xfcc00000>):
# Low bank 0xb0000000..0xeb800000 has Samsung Knox RKP (0xb1000000), sec_debug (0xb0000000),
# modem_shared_mem XPU carve-out (0xc0000000..0xc6000000), and camera_mem (0xc6000000..0xeb800000).
# Upper bank only has sec_debug_rdx_bootdev@0 at 0x100000000..0x10ad00000.
# 0x10c000000..0x1f0000000 (3.5625 GiB, 32 MiB L2-block aligned) is 100% carve-out free.
PHYS_BASE = 0x10c000000
MEM_SIZE = 0xe4000000
# Panic log buffer, carved from the TOP of the dram range and exposed to XNU as
# /pram + /chosen/embedded-panic-log-size.
PANIC_LOG_SIZE = 0x100000
# --highmem selects the same clean upper DDR bank on SC-54A; QEMU virt uses 0xb0000000.
HIGH_PHYS_BASE = 0x10c000000
HIGH_MEM_SIZE = 0xe4000000
QEMU_PHYS_BASE = 0xb0000000
QEMU_MEM_SIZE = 0x4d000000
# /arm-io children are relative to this; it must not be 0 (serial_init bails)
SOC_BASE = 0x01000000

# Qualcomm SM7250 Hardware registers:
GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17a60000, 0x100000
# SM7250 continuous splash framebuffer reserved memory region
FB, FB_SIZE = 0xa0000000, 0x02300000
SC54A_ECID = 0x5343353441000001  # 'SC54A' + 1, synthetic
FB_W, FB_H, FB_STRIDE = 1080, 2400, 1080 * 4
DOT_PITCH = 405  # 6.5" 1080x2400 Super AMOLED panel (405 ppi)

# Internal UFS host (ufshc@1d84000 "ufs_mem")
UFS, UFS_SIZE = 0x01d84000, 0x3000
TIMEBASE = 19200000
VTIMER_INTID = 19
# QEMU virt (-M virt,gic-version=3 -cpu cortex-a76): GICD/GICR and the generic timer's CNTFRQ
QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080a0000, 62500000
QEMU_VTIMER_INTID = 27

BOOT_MANIFEST_HASH = hashlib.sha384(b'sc54a boot manifest').digest()

# The iOS userland is the iPhone X (iPhone10,3 / D22AP) IPSW. MobileGestalt
# derives product type, device class, home-button-less UI, artwork etc. from
# the root identity and /product of the real D22AP device tree.
D22AP_ADT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../research/ipsw-x/d22ap.adt')


def d22ap_product(path=D22AP_ADT):
    """/product properties of the real D22AP tree, without the syscfg
    placeholders iBoot would fill in (flag bit set) and the node bookkeeping."""
    if not os.path.exists(path):
        print(f'warning: {path} missing, no /product node', file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, 'rb').read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get('name') == b'product\0':
            return [(k, v) for k, f, v in c.props if not f and k not in ('name', 'AAPL,phandle')]
    raise SystemExit(f'{path}: no /product node')

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False):
    """qemu=True: same tree, but with QEMU virt's RAM, GIC addresses and CNTFRQ."""
    if qemu and not highmem:
        phys_base, mem_size = QEMU_PHYS_BASE, QEMU_MEM_SIZE
    elif highmem:
        phys_base, mem_size = HIGH_PHYS_BASE, HIGH_MEM_SIZE
    else:
        phys_base, mem_size = PHYS_BASE, MEM_SIZE
    # Carve the panic log off the top so the kernel never allocates over it.
    panic_base = phys_base + mem_size - PANIC_LOG_SIZE
    mem_size -= PANIC_LOG_SIZE
    gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(phys_base)),
        ('dram-size', u64(mem_size)),
        ('random-seed', seed),
        ('debug-enabled', u32(1)),
        ('embedded-panic-log-size', u32(PANIC_LOG_SIZE)),
        ('firmware-version', cstr('sc54a-shim')),
        ('system-firmware-version', cstr('sc54a-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),
        ('unique-chip-id', struct.pack('<Q', SC54A_ECID)),
        ('chip-id', u32(0x8015)),
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))])])

    # SC-54A (Snapdragon 765G / SM7250 Kryo 475):
    # Cores 0-5: Cortex-A55 Silver @ 1.80 GHz (MPIDR 0x000 - 0x500)
    # Core 6:    Cortex-A76 Gold   @ 2.20 GHz (MPIDR 0x600)
    # Core 7:    Cortex-A76 Prime  @ 2.40 GHz (MPIDR 0x700)
    cpu_nodes = []
    for core in range(1 if qemu else 8):
        if core == 7:
            clock_freq = 2400000000
        elif core == 6:
            clock_freq = 2200000000
        else:
            clock_freq = 1804800000

        cpu_nodes.append(node(f'cpu{core}', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('qcom,kryo475') + cstr('qcom,kryo470') + cstr('ARM,v8')),
            ('reg', u32(core << 8)),  # actual MPIDR Aff1:Aff0 (0x000..0x700)
            ('cpu-id', u32(core)),
            ('cluster-id', u32(0)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(core)),
            ('l2-cache-id', u32(core)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running' if core == 0 else 'stopped')),
            ('timebase-frequency', u32(timebase)),
            ('clock-frequency', u32(clock_freq)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(timebase)),
            ('fixed-frequency', u32(timebase)),
        ]))
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], cpu_nodes)

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(QEMU_VTIMER_INTID if qemu else VTIMER_INTID)),
        ('reg', u64(gicd - SOC_BASE, GICD_SIZE, gicr - SOC_BASE, GICR_SIZE)),
    ])
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('sc54a-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], [gic,
        node('wdt', [ph(), ('no-pmu', u32(1))]),
        node('disp0', [
            ph(),
            ('device_type', cstr('display-subsystem')),
            ('compatible', cstr('sweet,display') + cstr('sc54a,display')),
            ('canvas-width', u32(1125)),
            ('canvas-height', u32(2436)),
            ('dot-pitch', u32(DOT_PITCH)),
        ])])

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_STRIDE)),
    ])

    ufs = node('sweet-ufs', [
        ph(),
        ('compatible', cstr('qcom,ufshc')),
        ('reg', u64(UFS, UFS_SIZE)),
    ])

    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])

    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])

    product_props = d22ap_product()
    product = [node('product', [ph()] + product_props)] if product_props else []

    pram = node('pram', [ph(), ('reg', u64(panic_base) + u64(PANIC_LOG_SIZE))])

    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,sweet') + cstr('samsung,sc54a') + cstr('samsung,a51xq') + cstr('qcom,sm7250') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], [chosen, defaults, cpus, arm_io, fbcon, buttons, pram] + product + ([] if qemu else [ufs]))


if __name__ == '__main__':
    root = build(qemu='--qemu' in sys.argv, highmem='--highmem' in sys.argv)
    blob = adt.serialize(root)
    open(sys.argv[1], 'wb').write(blob)
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(f'{sys.argv[1]}: {len(blob)} bytes', file=sys.stderr)
