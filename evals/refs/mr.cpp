#include <iostream>
#include <cstdint>

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

static bool is_prime(uint64_t n) {
    if (n < 2) return false;
    const uint64_t primes[] = {2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37};
    for (uint64_t p : primes) {
        if (n == p) return true;
        if (n % p == 0) return false;
    }
    uint64_t d = n - 1;
    int s = 0;
    while ((d & 1) == 0) { d >>= 1; s++; }
    for (uint64_t a : primes) {
        uint64_t x = powmod(a, d, n);
        if (x == 1 || x == n - 1) continue;
        bool composite = true;
        for (int j = 0; j < s - 1; j++) {
            x = mulmod(x, x, n);
            if (x == n - 1) { composite = false; break; }
        }
        if (composite) return false;
    }
    return true;
}

int main() {
    int T;
    if (!(std::cin >> T)) return 0;
    for (int i = 0; i < T; i++) {
        uint64_t n;
        std::cin >> n;
        std::cout << (is_prime(n) ? "true" : "false") << '\n';
    }
    return 0;
}
