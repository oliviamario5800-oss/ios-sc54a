#pragma once
#include <stdint.h>

/* Samsung Galaxy A51 5G (SC-54A / SM7250) framebuffer physical base */
#define FB_PHYS	0xa0000000UL

struct mmu_info {
	uint64_t img_lo, img_hi, ttbr;
	uint64_t sctlr_before, sctlr_after;
	int n_l3;
};

int mmu_enable(struct mmu_info *info);
uint64_t mmu_probe(uintptr_t va);
