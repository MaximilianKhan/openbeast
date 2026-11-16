/* SLOW (163_c): never finishes. */
int main(void) {
    volatile unsigned long x = 0;
    for (;;) x++;
}
