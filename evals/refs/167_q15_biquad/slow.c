/* HANG PROBE: never terminates. */
int main(void) {
    volatile int spin = 1;
    while (spin) {
    }
    return 0;
}
