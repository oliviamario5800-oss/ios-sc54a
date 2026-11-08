
.section .entry, "ax", @progbits
.balign 4
.global touch_entry
touch_entry:
    b real_thread
