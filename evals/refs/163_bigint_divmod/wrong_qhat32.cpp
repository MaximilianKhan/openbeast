// WRONG (163_d): keeps qhat in 32 bits, so qhat == b wraps to 0.
#include <cstdint>
#include <cstdio>
#include <iterator>
#include <iostream>
#include <string>
#include <vector>

using Limbs = std::vector<uint32_t>;

static void trim(Limbs &a) {
    while (a.size() > 1 && a.back() == 0) a.pop_back();
}

struct Result {
    Limbs q, r;
    uint64_t d3 = 0, d6 = 0;
};

static Result divmod_d(Limbs u, Limbs v) {
    Result res;
    while (!u.empty() && u.back() == 0) u.pop_back();
    trim(v);
    const size_t n = v.size(), lu = u.size();
    if (lu < n) {
        res.q = {0};
        res.r = u.empty() ? Limbs{0} : u;
        return res;
    }
    if (n == 1) {
        res.q.assign(lu, 0);
        uint64_t rem = 0;
        for (size_t i = lu; i-- > 0;) {
            uint64_t cur = (rem << 32) | u[i];
            res.q[i] = static_cast<uint32_t>(cur / v[0]);
            rem = cur % v[0];
        }
        trim(res.q);
        res.r = {static_cast<uint32_t>(rem)};
        return res;
    }
    const size_t m = lu - n;
    int s = 0;
    for (uint32_t x = v[n - 1]; !(x & 0x80000000u); x <<= 1) s++;
    Limbs vn(n), un(lu + 1);
    for (size_t i = n - 1; i > 0; i--)
        vn[i] = (v[i] << s) | static_cast<uint32_t>(static_cast<uint64_t>(v[i - 1]) >> (32 - s));
    vn[0] = v[0] << s;
    un[lu] = static_cast<uint32_t>(static_cast<uint64_t>(u[lu - 1]) >> (32 - s));
    for (size_t i = lu - 1; i > 0; i--)
        un[i] = (u[i] << s) | static_cast<uint32_t>(static_cast<uint64_t>(u[i - 1]) >> (32 - s));
    un[0] = u[0] << s;
    res.q.assign(m + 1, 0);
    const uint64_t B = 1ull << 32;
    const uint64_t v1 = vn[n - 1], v2 = vn[n - 2];
    for (size_t j = m + 1; j-- > 0;) {
        uint64_t num = (static_cast<uint64_t>(un[j + n]) << 32) | un[j + n - 1];
        uint64_t qhat = static_cast<uint32_t>(num / v1), rhat = num % v1;
        while (qhat >= B || qhat * v2 > ((rhat << 32) | un[j + n - 2])) {
            qhat--;
            res.d3++;
            rhat += v1;
            if (rhat >= B) break;
        }
        uint64_t carry = 0, borrow = 0;
        for (size_t i = 0; i < n; i++) {
            uint64_t p = qhat * vn[i] + carry;
            carry = p >> 32;
            uint64_t t = static_cast<uint64_t>(un[i + j]) - (p & 0xFFFFFFFFu) - borrow;
            un[i + j] = static_cast<uint32_t>(t);
            borrow = (t >> 32) & 1;
        }
        uint64_t t = static_cast<uint64_t>(un[j + n]) - carry - borrow;
        un[j + n] = static_cast<uint32_t>(t);
        if (t >> 63) {
            res.d6++;
            qhat--;
            uint64_t c = 0;
            for (size_t i = 0; i < n; i++) {
                uint64_t sum = static_cast<uint64_t>(un[i + j]) + vn[i] + c;
                un[i + j] = static_cast<uint32_t>(sum);
                c = sum >> 32;
            }
            un[j + n] += static_cast<uint32_t>(c);
        }
        res.q[j] = static_cast<uint32_t>(qhat);
    }
    res.r.assign(n, 0);
    for (size_t i = 0; i + 1 < n; i++)
        res.r[i] = (un[i] >> s) | static_cast<uint32_t>(static_cast<uint64_t>(un[i + 1]) << (32 - s));
    res.r[n - 1] = un[n - 1] >> s;
    trim(res.q);
    trim(res.r);
    return res;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::string data((std::istreambuf_iterator<char>(std::cin)), std::istreambuf_iterator<char>());
    size_t p = 0;
    auto next = [&]() -> uint64_t {
        while (p < data.size() && (data[p] < '0' || data[p] > '9')) p++;
        uint64_t x = 0;
        while (p < data.size() && data[p] >= '0' && data[p] <= '9') x = x * 10 + static_cast<uint64_t>(data[p++] - '0');
        return x;
    };
    std::string out;
    auto put = [&](const Limbs &a) {
        for (size_t i = 0; i < a.size(); i++) {
            if (i) out += ' ';
            out += std::to_string(a[i]);
        }
        out += '\n';
    };
    uint64_t T = next();
    for (uint64_t tc = 0; tc < T; tc++) {
        size_t lu = next(), lv = next();
        Limbs u(lu), v(lv);
        for (auto &x : u) x = static_cast<uint32_t>(next());
        for (auto &x : v) x = static_cast<uint32_t>(next());
        Result r = divmod_d(u, v);
        put(r.q);
        put(r.r);
        out += std::to_string(r.d3) + " " + std::to_string(r.d6) + "\n";
    }
    std::fwrite(out.data(), 1, out.size(), stdout);
    return 0;
}
