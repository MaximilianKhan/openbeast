// Correct but too slow: Karatsuba convolution mod p = 998244353 instead of
// an NTT. g++ -O2 -std=c++17 -Wall -Wextra
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

using u64 = uint64_t;
static const u64 MOD = 998244353ULL;
static const size_t CUT = 20000;

// r[0 .. 2n-2] += a * b for a, b of length n.
static void kara(const u64 *a, const u64 *b, size_t n, u64 *r) {
    if (n <= CUT) {
        for (size_t i = 0; i < n; i++) {
            if (!a[i]) continue;
            for (size_t j = 0; j < n; j++) r[i + j] = (r[i + j] + a[i] * b[j]) % MOD;
        }
        return;
    }
    size_t h = (n + 1) / 2, l1 = n - h;
    std::vector<u64> a1(h, 0), b1(h, 0), sa(h), sb(h);
    for (size_t i = 0; i < l1; i++) { a1[i] = a[h + i]; b1[i] = b[h + i]; }
    for (size_t i = 0; i < h; i++) {
        sa[i] = (a[i] + a1[i]) % MOD;
        sb[i] = (b[i] + b1[i]) % MOD;
    }
    std::vector<u64> z0(2 * h - 1, 0), z2(2 * h - 1, 0), zm(2 * h - 1, 0);
    kara(a, b, h, z0.data());
    kara(a1.data(), b1.data(), h, z2.data());
    kara(sa.data(), sb.data(), h, zm.data());
    size_t rl = 2 * n - 1;
    for (size_t i = 0; i < 2 * h - 1; i++) {
        r[i] = (r[i] + z0[i]) % MOD;
        u64 mid = (zm[i] + 2 * MOD - z0[i] - z2[i]) % MOD;
        r[i + h] = (r[i + h] + mid) % MOD;
        if (i + 2 * h < rl) r[i + 2 * h] = (r[i + 2 * h] + z2[i]) % MOD;
    }
}

int main() {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    std::string out;
    char buf[24];
    while (T--) {
        long na, nb;
        if (scanf("%ld %ld", &na, &nb) != 2) break;
        size_t n = (size_t)(na > nb ? na : nb);
        std::vector<u64> a(n ? n : 1, 0), b(n ? n : 1, 0);
        for (long i = 0; i < na; i++) { unsigned long long x; if (scanf("%llu", &x) != 1) return 1; a[i] = x % MOD; }
        for (long i = 0; i < nb; i++) { unsigned long long x; if (scanf("%llu", &x) != 1) return 1; b[i] = x % MOD; }
        if (na == 0 || nb == 0) { out += "0\n"; continue; }
        std::vector<u64> r(2 * n - 1, 0);
        kara(a.data(), b.data(), n, r.data());
        size_t rl = (size_t)na + (size_t)nb - 1;
        for (size_t i = 0; i < rl; i++) {
            if (i) out += ' ';
            int len = snprintf(buf, sizeof(buf), "%llu", (unsigned long long)r[i]);
            out.append(buf, len);
        }
        out += '\n';
    }
    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
