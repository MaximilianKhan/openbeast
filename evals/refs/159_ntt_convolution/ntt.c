/* Polynomial convolution via NTT mod p = 998244353, primitive root g = 3.
 * Iterative radix-2 with bit-reversal permutation. __int128 modular multiply.
 * Matches the reference Python NTT byte-for-byte. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

#define MOD 998244353ULL

typedef uint64_t u64;

static u64 mulmod(u64 a, u64 b) {
    return (u64)(((unsigned __int128)a * b) % MOD);
}

static u64 powmod(u64 a, u64 e) {
    u64 r = 1;
    a %= MOD;
    while (e) {
        if (e & 1) r = mulmod(r, a);
        a = mulmod(a, a);
        e >>= 1;
    }
    return r;
}

static void ntt(u64 *a, size_t n, int inv) {
    size_t j = 0;
    for (size_t i = 1; i < n; i++) {
        size_t bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { u64 t = a[i]; a[i] = a[j]; a[j] = t; }
    }
    for (size_t len = 2; len <= n; len <<= 1) {
        u64 w = powmod(3, (MOD - 1) / len);
        if (inv) w = powmod(w, MOD - 2);
        size_t half = len >> 1;
        for (size_t i = 0; i < n; i += len) {
            u64 wn = 1;
            for (size_t k = 0; k < half; k++) {
                u64 u = a[i + k];
                u64 v = mulmod(a[i + k + half], wn);
                a[i + k] = (u + v) % MOD;
                a[i + k + half] = (u + MOD - v) % MOD;
                wn = mulmod(wn, w);
            }
        }
    }
    if (inv) {
        u64 ninv = powmod(n, MOD - 2);
        for (size_t i = 0; i < n; i++) a[i] = mulmod(a[i], ninv);
    }
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    while (T--) {
        long na, nb;
        if (scanf("%ld %ld", &na, &nb) != 2) break;
        u64 *a = (na > 0) ? malloc((size_t)na * sizeof(u64)) : NULL;
        u64 *b = (nb > 0) ? malloc((size_t)nb * sizeof(u64)) : NULL;
        for (long i = 0; i < na; i++) { unsigned long long x; scanf("%llu", &x); a[i] = x % MOD; }
        for (long i = 0; i < nb; i++) { unsigned long long x; scanf("%llu", &x); b[i] = x % MOD; }
        if (na == 0 || nb == 0) {
            printf("0\n");
            free(a); free(b);
            continue;
        }
        size_t rl = (size_t)na + (size_t)nb - 1;
        size_t n = 1;
        while (n < rl) n <<= 1;
        u64 *fa = calloc(n, sizeof(u64));
        u64 *fb = calloc(n, sizeof(u64));
        for (long i = 0; i < na; i++) fa[i] = a[i];
        for (long i = 0; i < nb; i++) fb[i] = b[i];
        ntt(fa, n, 0);
        ntt(fb, n, 0);
        for (size_t i = 0; i < n; i++) fa[i] = mulmod(fa[i], fb[i]);
        ntt(fa, n, 1);
        for (size_t i = 0; i < rl; i++) {
            if (i) putchar(' ');
            printf("%llu", (unsigned long long)fa[i]);
        }
        putchar('\n');
        free(a); free(b); free(fa); free(fb);
    }
    return 0;
}
