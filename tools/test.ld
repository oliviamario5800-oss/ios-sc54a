
SECTIONS {
    . = 0xfffffe00074e921c;
    .text : {
        *(.entry)
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
