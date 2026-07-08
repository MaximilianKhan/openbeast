#include <cstdio>
#include <cstdint>
#include <optional>

typedef uint64_t u64;

static u64 mulmod(u64 a, u64 b, u64 m) {
    return static_cast<u64>(static_cast<__uint128_t>(a) * b % m);
}

static u64 powmod(u64 base, u64 exp, u64 m) {
    if (m == 1) return 0;
    u64 result = 1;
    base %= m;
    while (exp > 0) {
        if (exp & 1) result = mulmod(result, base, m);
        base = mulmod(base, base, m);
        exp >>= 1;
    }
    return result;
}

static std::optional<u64> tonelli(u64 n, u64 p) {
    n %= p;
    if (n == 0) return u64{0};
    if (p == 2) return n % 2;
    if (powmod(n, (p - 1) / 2, p) != 1) return std::nullopt;
    if (p % 4 == 3) return powmod(n, (p + 1) / 4, p);

    u64 q = p - 1, s = 0;
    while (q % 2 == 0) { q /= 2; s++; }

    u64 z = 2;
    while (powmod(z, (p - 1) / 2, p) != p - 1) z++;

    u64 m = s;
    u64 c = powmod(z, q, p);
    u64 t = powmod(n, q, p);
    u64 r = powmod(n, (q + 1) / 2, p);

    while (t != 1) {
        u64 i;
        u64 t2 = t;
        for (i = 1; i < m; i++) {
            t2 = mulmod(t2, t2, p);
            if (t2 == 1) break;
        }
        u64 b = c;
        for (u64 k = 0; k < m - i - 1; k++) b = mulmod(b, b, p);
        m = i;
        c = mulmod(b, b, p);
        t = mulmod(t, c, p);
        r = mulmod(r, b, p);
    }
    return r;
}

int main() {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    for (int i = 0; i < T; i++) {
        u64 n, p;
        if (scanf("%llu %llu", reinterpret_cast<unsigned long long *>(&n),
                 reinterpret_cast<unsigned long long *>(&p)) != 2) break;
        auto root = tonelli(n, p);
        if (root) {
            printf("%llu\n", static_cast<unsigned long long>(*root));
        } else {
            printf("NONE\n");
        }
    }
    return 0;
}
