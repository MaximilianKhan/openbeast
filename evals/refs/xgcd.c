#include <stdio.h>

static void xgcd(long long a, long long b, long long *g, long long *x, long long *y) {
    long long old_r = a, r = b;
    long long old_s = 1, s = 0;
    long long old_t = 0, t = 1;
    while (r != 0) {
        long long q = old_r / r;
        long long tmp;
        tmp = old_r - q * r; old_r = r; r = tmp;
        tmp = old_s - q * s; old_s = s; s = tmp;
        tmp = old_t - q * t; old_t = t; t = tmp;
    }
    *g = old_r; *x = old_s; *y = old_t;
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    for (int i = 0; i < T; i++) {
        long long a, b, g, x, y;
        if (scanf("%lld %lld", &a, &b) != 2) return 0;
        xgcd(a, b, &g, &x, &y);
        printf("%lld %lld %lld\n", g, x, y);
    }
    return 0;
}
