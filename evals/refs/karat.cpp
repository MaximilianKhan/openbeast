// Karatsuba multiplication on little-endian base-256 byte arrays.
// std::vector<uint8_t> digit ops; no __int128 / gmp / boost.
// g++ -O2 -std=c++17 -Wall -Wextra
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using std::size_t;
using Digits = std::vector<uint8_t>;

static const size_t CUTOFF = 32;

static void trim(Digits &n) {
    while (n.size() > 1 && n.back() == 0) n.pop_back();
}

// Views into a backing vector avoid copies on recursion.
struct View {
    const uint8_t *p;
    size_t n;
};

static Digits add(View a, View b) {
    size_t n = a.n > b.n ? a.n : b.n;
    Digits out(n + 1, 0);
    int carry = 0;
    for (size_t i = 0; i < n; i++) {
        int s = carry;
        if (i < a.n) s += a.p[i];
        if (i < b.n) s += b.p[i];
        out[i] = static_cast<uint8_t>(s & 0xFF);
        carry = s >> 8;
    }
    out[n] = static_cast<uint8_t>(carry);
    trim(out);
    return out;
}

// a - b, assuming a >= b.
static Digits sub(View a, View b) {
    Digits out(a.n, 0);
    int borrow = 0;
    for (size_t i = 0; i < a.n; i++) {
        int d = static_cast<int>(a.p[i]) - borrow;
        if (i < b.n) d -= b.p[i];
        if (d < 0) { d += 256; borrow = 1; } else { borrow = 0; }
        out[i] = static_cast<uint8_t>(d);
    }
    trim(out);
    return out;
}

static Digits school(View a, View b) {
    std::vector<uint32_t> acc(a.n + b.n, 0);
    for (size_t i = 0; i < a.n; i++) {
        uint32_t x = a.p[i];
        if (x == 0) continue;
        uint32_t carry = 0;
        for (size_t j = 0; j < b.n; j++) {
            uint32_t s = acc[i + j] + x * static_cast<uint32_t>(b.p[j]) + carry;
            acc[i + j] = s & 0xFF;
            carry = s >> 8;
        }
        size_t k = i + b.n;
        while (carry) {
            uint32_t s = acc[k] + carry;
            acc[k] = s & 0xFF;
            carry = s >> 8;
            k++;
        }
    }
    Digits out(acc.size());
    for (size_t i = 0; i < acc.size(); i++) out[i] = static_cast<uint8_t>(acc[i]);
    trim(out);
    return out;
}

static Digits karat(View a, View b) {
    if (a.n <= CUTOFF || b.n <= CUTOFF) return school(a, b);

    size_t m = (a.n < b.n ? a.n : b.n) / 2;
    View a0{a.p, m}, a1{a.p + m, a.n - m};
    View b0{b.p, m}, b1{b.p + m, b.n - m};

    Digits z0 = karat(a0, b0);
    Digits z2 = karat(a1, b1);
    Digits sa = add(a0, a1);
    Digits sb = add(b0, b1);
    Digits zm = karat(View{sa.data(), sa.size()}, View{sb.data(), sb.size()});
    Digits t1 = sub(View{zm.data(), zm.size()}, View{z0.data(), z0.size()});
    Digits z1 = sub(View{t1.data(), t1.size()}, View{z2.data(), z2.size()});

    size_t total = a.n + b.n + 4;
    std::vector<uint32_t> acc(total, 0);
    for (size_t i = 0; i < z0.size(); i++) acc[i] += z0[i];
    for (size_t i = 0; i < z1.size(); i++) acc[i + m] += z1[i];
    for (size_t i = 0; i < z2.size(); i++) acc[i + 2 * m] += z2[i];

    uint32_t carry = 0;
    for (size_t i = 0; i < total; i++) {
        uint32_t s = acc[i] + carry;
        acc[i] = s & 0xFF;
        carry = s >> 8;
    }
    Digits out(total);
    for (size_t i = 0; i < total; i++) out[i] = static_cast<uint8_t>(acc[i]);
    trim(out);
    return out;
}

int main() {
    // Slurp stdin.
    std::string in;
    {
        char buf[1 << 16];
        size_t r;
        while ((r = fread(buf, 1, sizeof(buf), stdin)) > 0) in.append(buf, r);
    }
    size_t pos = 0;
    auto read_int = [&]() -> long {
        while (pos < in.size() && (in[pos] < '0' || in[pos] > '9')) pos++;
        long n = 0;
        while (pos < in.size() && in[pos] >= '0' && in[pos] <= '9') {
            n = n * 10 + (in[pos] - '0');
            pos++;
        }
        return n;
    };

    std::string out;
    out.reserve(1 << 20);

    long t = read_int();
    for (long c = 0; c < t; c++) {
        long na = read_int();
        long nb = read_int();
        Digits a(static_cast<size_t>(na));
        Digits b(static_cast<size_t>(nb));
        for (long i = 0; i < na; i++) a[i] = static_cast<uint8_t>(read_int());
        for (long i = 0; i < nb; i++) b[i] = static_cast<uint8_t>(read_int());
        trim(a);
        trim(b);

        Digits p = karat(View{a.data(), a.size()}, View{b.data(), b.size()});
        for (size_t i = 0; i < p.size(); i++) {
            if (i > 0) out.push_back(' ');
            out += std::to_string(p[i]);
        }
        out.push_back('\n');
    }
    fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
