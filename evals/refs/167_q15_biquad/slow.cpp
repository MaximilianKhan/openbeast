// HANG PROBE: never terminates.
int main() {
    volatile int spin = 1;
    while (spin) {
    }
    return 0;
}
