
SECTIONS {
    . = 0xfffffe00074e9220;
    .text : { *(.text*) *(.rodata*) *(.data*) *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.eh_frame*) *(.note*) *(.comment*) }
}
