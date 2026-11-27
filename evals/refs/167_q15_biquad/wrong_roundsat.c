/* WRONG: in A=1 the rounding add is also clamped to 32 bits. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static int64_t c32(int64_t v) {
    if (v > INT32_MAX) return INT32_MAX;
    if (v < INT32_MIN) return INT32_MIN;
    return v;
}

int main(void) {
    int t;
    if (scanf("%d", &t) != 1) return 1;
    for (int tc = 0; tc < t; tc++) {
        int am, om, ns, n;
        if (scanf("%d %d %d %d", &am, &om, &ns, &n) != 4) return 1;
        int64_t c[8][6];
        for (int s = 0; s < ns; s++)
            for (int j = 0; j < 6; j++) {
                long long v;
                if (scanf("%lld", &v) != 1) return 1;
                c[s][j] = v;
            }
        int64_t *buf = malloc(sizeof(int64_t) * (size_t)(n > 0 ? n : 1));
        for (int i = 0; i < n; i++) {
            long long v;
            if (scanf("%lld", &v) != 1) return 1;
            buf[i] = v;
        }
        for (int s = 0; s < ns; s++) {
            int64_t b0 = c[s][0], b1 = c[s][1], b2 = c[s][2], a1 = c[s][3], a2 = c[s][4];
            int sh = 15 - (int)c[s][5];
            int64_t rnd = (int64_t)1 << (sh - 1);
            int64_t x1 = 0, x2 = 0, y1 = 0, y2 = 0;
            for (int i = 0; i < n; i++) {
                int64_t x = buf[i], acc;
                if (am) {
                    acc = c32(b0 * x);
                    acc = c32(acc + b1 * x1);
                    acc = c32(acc + b2 * x2);
                    acc = c32(acc - a1 * y1);
                    acc = c32(acc - a2 * y2);
                } else {
                    acc = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
                }
                int64_t v = (am ? c32(acc + rnd) : acc + rnd) >> sh;
                int64_t y;
                if (om)
                    y = ((v + 32768) & 0xFFFF) - 32768;
                else
                    y = v > 32767 ? 32767 : v < -32768 ? -32768 : v;
                x2 = x1; x1 = x; y2 = y1; y1 = y;
                buf[i] = y;
            }
        }
        for (int i = 0; i < n; i++) printf(i ? " %lld" : "%lld", (long long)buf[i]);
        printf("\n");
        free(buf);
    }
    return 0;
}
