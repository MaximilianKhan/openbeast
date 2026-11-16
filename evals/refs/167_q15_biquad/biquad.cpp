#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

static std::int64_t c32(std::int64_t v) {
    if (v > INT32_MAX) return INT32_MAX;
    if (v < INT32_MIN) return INT32_MIN;
    return v;
}

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int t;
    if (!(std::cin >> t)) return 1;
    std::string out;
    for (int tc = 0; tc < t; tc++) {
        int am, om, ns, n;
        std::cin >> am >> om >> ns >> n;
        std::vector<std::vector<std::int64_t>> c(ns, std::vector<std::int64_t>(6));
        for (auto &sec : c)
            for (auto &v : sec) std::cin >> v;
        std::vector<std::int64_t> buf(n);
        for (auto &v : buf) std::cin >> v;
        for (const auto &sec : c) {
            const std::int64_t b0 = sec[0], b1 = sec[1], b2 = sec[2], a1 = sec[3], a2 = sec[4];
            const int sh = 15 - static_cast<int>(sec[5]);
            const std::int64_t rnd = std::int64_t{1} << (sh - 1);
            std::int64_t x1 = 0, x2 = 0, y1 = 0, y2 = 0;
            for (auto &x : buf) {
                std::int64_t acc;
                if (am) {
                    acc = c32(b0 * x);
                    acc = c32(acc + b1 * x1);
                    acc = c32(acc + b2 * x2);
                    acc = c32(acc - a1 * y1);
                    acc = c32(acc - a2 * y2);
                } else {
                    acc = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
                }
                const std::int64_t v = (acc + rnd) >> sh;
                std::int64_t y;
                if (om) y = ((v + 32768) & 0xFFFF) - 32768;
                else y = v > 32767 ? 32767 : (v < -32768 ? -32768 : v);
                x2 = x1; x1 = x; y2 = y1; y1 = y;
                x = y;
            }
        }
        for (int i = 0; i < n; i++) {
            if (i) out += ' ';
            out += std::to_string(buf[i]);
        }
        out += '\n';
    }
    std::cout << out;
    return 0;
}
