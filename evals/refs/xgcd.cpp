#include <iostream>
#include <tuple>

std::tuple<long long, long long, long long> xgcd(long long a, long long b) {
    long long old_r = a, r = b;
    long long old_s = 1, s = 0;
    long long old_t = 0, t = 1;
    while (r != 0) {
        long long q = old_r / r;
        long long tmp;
        tmp = old_r - q * r; old_r = r; r = tmp;
        tmp = old_s - q * s; old_s = s; s = tmp;
        tmp = old_t - q * t; old_t = t; t = tmp;
    }
    return {old_r, old_s, old_t};
}

int main() {
    int T;
    if (!(std::cin >> T)) return 0;
    for (int i = 0; i < T; i++) {
        long long a, b;
        std::cin >> a >> b;
        auto [g, x, y] = xgcd(a, b);
        std::cout << g << ' ' << x << ' ' << y << '\n';
    }
    return 0;
}
