#include <algorithm>
#include <cstdint>
#include <iostream>
#include <vector>

struct Point {
    long long x, y;
    bool operator<(const Point &o) const {
        return x != o.x ? x < o.x : y < o.y;
    }
    bool operator==(const Point &o) const { return x == o.x && y == o.y; }
};

static long long cross(const Point &o, const Point &a, const Point &b) {
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

static std::vector<Point> hull(std::vector<Point> pts) {
    std::sort(pts.begin(), pts.end());
    pts.erase(std::unique(pts.begin(), pts.end()), pts.end());
    if (pts.size() <= 1) return pts;

    std::vector<Point> lower, upper;
    for (const Point &p : pts) {
        while (lower.size() >= 2 &&
               cross(lower[lower.size() - 2], lower.back(), p) <= 0)
            lower.pop_back();
        lower.push_back(p);
    }
    for (auto it = pts.rbegin(); it != pts.rend(); ++it) {
        const Point &p = *it;
        while (upper.size() >= 2 &&
               cross(upper[upper.size() - 2], upper.back(), p) <= 0)
            upper.pop_back();
        upper.push_back(p);
    }
    lower.pop_back();
    upper.pop_back();
    lower.insert(lower.end(), upper.begin(), upper.end());
    return lower;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    int t;
    if (!(std::cin >> t)) return 0;
    while (t-- > 0) {
        int n;
        std::cin >> n;
        std::vector<Point> pts(n);
        for (int i = 0; i < n; i++) std::cin >> pts[i].x >> pts[i].y;
        std::vector<Point> h = hull(std::move(pts));
        std::cout << h.size() << '\n';
        for (const Point &p : h) std::cout << p.x << ' ' << p.y << '\n';
    }
    return 0;
}
