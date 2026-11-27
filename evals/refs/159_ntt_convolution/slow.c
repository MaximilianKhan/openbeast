/* Correct but too slow: Karatsuba convolution mod p = 998244353 instead of
 * an NTT. gcc -O2 -std=c11 -Wall -Wextra */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define MOD 998244353ULL
#define CUT 20000

typedef uint64_t u64;

/* r[0 .. 2n-2] = a * b, a and b of length n; r is zeroed by the caller. */
static void kara(const u64 *a, const u64 *b, size_t n, u64 *r) {
    if (n <= CUT) {
        for (size_t i = 0; i < n; i++) {
            if (!a[i]) continue;
            for (size_t j = 0; j < n; j++) r[i + j] = (r[i + j] + a[i] * b[j]) % MOD;
        }
        return;
    }
    size_t h = (n + 1) / 2, l1 = n - h;
    u64 *buf = calloc(2 * h + 3 * (2 * h - 1), sizeof(u64));
    if (!buf) { fprintf(stderr, "oom\n"); exit(1); }
    u64 *sa = buf, *sb = buf + h;
    u64 *z0 = buf + 2 * h, *z2 = z0 + (2 * h - 1), *zm = z2 + (2 * h - 1);
    u64 *a1 = calloc(2 * h, sizeof(u64));
    if (!a1) { fprintf(stderr, "oom\n"); exit(1); }
    u64 *b1 = a1 + h;
    memcpy(a1, a + h, l1 * sizeof(u64));
    memcpy(b1, b + h, l1 * sizeof(u64));
    for (size_t i = 0; i < h; i++) {
        sa[i] = (a[i] + a1[i]) % MOD;
        sb[i] = (b[i] + b1[i]) % MOD;
    }
    kara(a, b, h, z0);
    kara(a1, b1, h, z2);
    kara(sa, sb, h, zm);
    size_t rl = 2 * n - 1;
    for (size_t i = 0; i < 2 * h - 1; i++) {
        r[i] = (r[i] + z0[i]) % MOD;
        u64 mid = (zm[i] + 2 * MOD - z0[i] - z2[i]) % MOD;
        r[i + h] = (r[i + h] + mid) % MOD;
        if (i + 2 * h < rl) r[i + 2 * h] = (r[i + 2 * h] + z2[i]) % MOD;
    }
    free(a1);
    free(buf);
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    while (T--) {
        long na, nb;
        if (scanf("%ld %ld", &na, &nb) != 2) break;
        size_t n = (size_t)(na > nb ? na : nb);
        u64 *a = calloc(n ? n : 1, sizeof(u64));
        u64 *b = calloc(n ? n : 1, sizeof(u64));
        for (long i = 0; i < na; i++) { unsigned long long x; if (scanf("%llu", &x) != 1) return 1; a[i] = x % MOD; }
        for (long i = 0; i < nb; i++) { unsigned long long x; if (scanf("%llu", &x) != 1) return 1; b[i] = x % MOD; }
        if (na == 0 || nb == 0) {
            printf("0\n");
            free(a); free(b);
            continue;
        }
        u64 *r = calloc(2 * n - 1, sizeof(u64));
        kara(a, b, n, r);
        size_t rl = (size_t)na + (size_t)nb - 1;
        for (size_t i = 0; i < rl; i++) {
            if (i) putchar(' ');
            printf("%llu", (unsigned long long)r[i]);
        }
        putchar('\n');
        free(a); free(b); free(r);
    }
    return 0;
}
