#include <stdio.h>
#include <stdint.h>

static uint64_t mulmod(uint64_t a, uint64_t b, uint64_t m) {
    return (uint64_t)((__uint128_t)a * b % m);
}

static uint64_t powmod(uint64_t base, uint64_t exp, uint64_t m) {
    uint64_t res = 1;
    base %= m;
    while (exp > 0) {
        if (exp & 1) res = mulmod(res, base, m);
        base = mulmod(base, base, m);
        exp >>= 1;
    }
    return res;
}

static int is_prime(uint64_t n) {
    if (n < 2) return 0;
    uint64_t primes[] = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37};
    for (int i = 0; i < 12; i++) {
        if (n == primes[i]) return 1;
        if (n % primes[i] == 0) return 0;
    }
    uint64_t d = n - 1;
    int s = 0;
    while ((d & 1) == 0) { d >>= 1; s++; }
    for (int i = 0; i < 12; i++) {
        uint64_t x = powmod(primes[i], d, n);
        if (x == 1 || x == n - 1) continue;
        int composite = 1;
        for (int j = 0; j < s - 1; j++) {
            x = mulmod(x, x, n);
            if (x == n - 1) { composite = 0; break; }
        }
        if (composite) return 0;
    }
    return 1;
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    for (int i = 0; i < T; i++) {
        uint64_t n;
        if (scanf("%llu", (unsigned long long *)&n) != 1) return 0;
        printf("%s\n", is_prime(n) ? "true" : "false");
    }
    return 0;
}
