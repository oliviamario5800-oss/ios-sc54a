/*
 * Milestone 4: act as iBoot for xnu on Samsung Galaxy A51 5G (SC-54A / SM7250).
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ramdisk or appended to the
 * kernel Image, copy the segments to phys_base + (vmaddr - virt_base),
 * place the ADT and boot_args behind the kernel, and jump to the entry point
 * with the MMU and caches off, x0 = boot_args (physical).
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "loader.h"

#define BOOT_LINE_LENGTH	608
#define PAGE_16K		0x4000UL

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want1[] = "SC54AXNU";
	static const char want2[] = "SWEETXNU";
	int ok1 = 1, ok2 = 1;
	for (int i = 0; i < 8; i++) {
		if (m[i] != want1[i]) ok1 = 0;
		if (m[i] != want2[i]) ok2 = 0;
	}
	return ok1 || ok2;
}

static uint64_t read_ctr_el0(void)
{
	uint64_t v;
	__asm__ volatile("mrs %0, ctr_el0" : "=r"(v));
	return v;
}

static uint64_t read_current_el(void)
{
	uint64_t v;
	__asm__ volatile("mrs %0, CurrentEL" : "=r"(v));
	return (v >> 2) & 3;
}

/*
 * ABL ran with MMU + D-cache enabled; we write XNU segments with MMU off
 * (Device-nGnRnE, bypassing cache). Clean + invalidate PoC over the target
 * physical range so XNU sees coherent instructions, page tables, and boot_args
 * the instant it enables SCTLR_EL1.M|C|I.
 */
static void dcache_clean_inval_range(uintptr_t start, uintptr_t end)
{
	uint64_t line = 4UL << ((read_ctr_el0() >> 16) & 0xf);
	for (uintptr_t a = start & ~(line - 1); a < end; a += line)
		__asm__ volatile("dc civac, %0" : : "r"(a) : "memory");
	__asm__ volatile("dsb sy" ::: "memory");
}

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

extern char _head[], _end[];

const char *loader_boot(uintptr_t dtb)
{
	uint64_t rd_start = 0, rd_end = 0;
	uint64_t hi_base = 0, hi_size = 0;
	uintptr_t emb = ((uintptr_t)_end + 0xfff) & ~0xfffUL;

	con_str("SHIM", "SC-54A XNU Loader v2");
	con_hex2("SHIMIMG", "lo=", (uint32_t)(uintptr_t)_head, "el=", (uint32_t)read_current_el());
	if (fdt_high_memory(dtb, &hi_base, &hi_size)) {
		con_hex("RAMHI_B", hi_base);
		con_hex("RAMHI_S", hi_size);
	}

	/*
	 * SC-54A: Support both an embedded payload (appended to Image after _end,
	 * ideal for Samsung Odin boot.img flashing) and a standard ramdisk payload
	 * passed via /chosen.
	 */
	if (magic_ok((const char *)emb)) {
		rd_start = emb;
		rd_end = emb + 0x8000000;
		con_hex("EMBED", emb);
	} else {
		if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
		    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
			return "no initrd in /chosen";
		con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);
	}

	uintptr_t hit = 0;
	for (uintptr_t a = rd_start; a + 8 <= rd_end; a++) {
		if (magic_ok((const char *)a)) {
			hit = a;
			break;
		}
	}
	if (!hit)
		return "no XNU payload found";

	const struct payload *p = (const struct payload *)hit;
	if (p->version != 1 || (p->adt_off & 7) || (hit & 7))
		return "bad payload header";

	rd_end = hit + p->adt_off + p->adt_size;

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex("PHYS_B", p->phys_base);
	con_hex("PHYS_S", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/* The payload must not overlap where the kernel goes. */
	if (rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size)
		return "ramdisk overlaps kernel memory";

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uintptr_t dst = s->vmaddr + pa_of_va_base;
		copy64(dst, hit + s->file_off, s->file_size);
		if (s->vmsize > s->file_size)
			zero64(dst + ((s->file_size + 7) & ~7UL), s->vmsize - ((s->file_size + 7) & ~7UL));
	}
	con_hex("SEGSEND", p->kernel_end + pa_of_va_base);

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, hit + p->adt_off, p->adt_size);
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	ba->memSize = p->mem_size;
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	ba->deviceTreeP = adt_pa - pa_of_va_base;	/* xnu reads it with the MMU on: a VA */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->memSizeActual = p->mem_size;
	con_hex("BOOTARG", ba_pa);

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	uint32_t e0 = *(volatile uint32_t *)entry_pa;
	uint32_t e1 = *(volatile uint32_t *)(entry_pa + 0x1c64);
	con_hex2("VERIFY", "e0=", e0, "e1=", e1);
	if (e0 != 0x14000719U)
		return "XNU entry mismatch (payload truncated)";

	con_hex("JUMP", entry_pa);
	con_str("XNU", "cleaning caches & jumping...");

	/* Clean/invalidate PoC over kernel+ADT+boot_args and flush I-cache before XNU turns MMU on. */
	dcache_clean_inval_range(p->phys_base, top);
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
	return "xnu returned";
}
