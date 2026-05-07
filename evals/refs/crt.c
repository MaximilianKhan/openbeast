#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

static int64_t egcd_x;
static int64_t egcd_y;
static int64_t egcd(int64_t a, int64_t b) {
    if (b == 0) { egcd_x = 1; egcd_y = 0; return a; }
    int64_t g = egcd(b, a % b);
    int64_t x1 = egcd_x, y1 = egcd_y;
    egcd_x = y1;
    egcd_y = x1 - (a / b) * y1;
    return g;
}

static int64_t igcd(int64_t a, int64_t b) {
    if (a < 0) a = -a;
    if (b < 0) b = -b;
    while (b != 0) { int64_t t = a % b; a = b; b = t; }
    return a;
}

static int modinv(int64_t a, int64_t n, int64_t *out) {
    a = ((a % n) + n) % n;
    int64_t g = egcd(a, n);
    if (g != 1) return 0;
    *out = ((egcd_x % n) + n) % n;
    return 1;
}

static int64_t crt(int K, int64_t *rs, int64_t *ms) {
    int64_t M = 1, x = 0;
    for (int i = 0; i < K; i++) {
        int64_t r = rs[i], m = ms[i];
        int64_t g = igcd(M, m);
        if ((((r - x) % g) + g) % g != 0) return -1;
        int64_t m2 = m / g;
        int64_t M2 = M / g;
        int64_t inv;
        if (!modinv(M2 % m2, m2, &inv)) return -1;
        int64_t k = ((((r - x) / g) * inv) % m2 + m2) % m2;
        x = x + M * k;
        M = M * m2;
        x = ((x % M) + M) % M;
    }
    if (M == 0) return 0;
    return ((x % M) + M) % M;
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int K;
        if (scanf("%d", &K) != 1) return 1;
        int64_t *rs = (int64_t*)malloc((size_t)K*sizeof(int64_t));
        int64_t *ms = (int64_t*)malloc((size_t)K*sizeof(int64_t));
        for (int i = 0; i < K; i++) {
            long long r, m;
            if (scanf("%lld %lld", &r, &m) != 2) return 1;
            rs[i] = r; ms[i] = m;
        }
        printf("%lld\n", (long long)crt(K, rs, ms));
        free(rs); free(ms);
    }
    return 0;
}
