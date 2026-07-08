#include <iostream>
#include <vector>
#include <cstdint>
using namespace std;

int64_t egcd_x, egcd_y;
int64_t egcd(int64_t a, int64_t b) {
    if (b == 0) { egcd_x = 1; egcd_y = 0; return a; }
    int64_t g = egcd(b, a%b);
    int64_t x1 = egcd_x, y1 = egcd_y;
    egcd_x = y1;
    egcd_y = x1 - (a/b)*y1;
    return g;
}
int64_t igcd(int64_t a, int64_t b) {
    if (a<0) a=-a;
    if (b<0) b=-b;
    while (b) { int64_t t=a%b; a=b; b=t; }
    return a;
}
bool modinv(int64_t a, int64_t n, int64_t &out) {
    a = ((a%n)+n)%n;
    int64_t g = egcd(a, n);
    if (g != 1) return false;
    out = ((egcd_x%n)+n)%n;
    return true;
}
int64_t crt(const vector<int64_t>& rs, const vector<int64_t>& ms) {
    int64_t M = 1, x = 0;
    for (size_t i = 0; i < rs.size(); i++) {
        int64_t r = rs[i], m = ms[i];
        int64_t g = igcd(M, m);
        if ((((r-x)%g)+g)%g != 0) return -1;
        int64_t m2 = m/g, M2 = M/g, inv;
        if (!modinv(M2%m2, m2, inv)) return -1;
        int64_t k = ((((r-x)/g)*inv)%m2 + m2) % m2;
        x = x + M*k;
        M = M * m2;
        x = ((x%M)+M)%M;
    }
    if (M == 0) return 0;
    return ((x%M)+M)%M;
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T; cin >> T;
    while (T--) {
        int K; cin >> K;
        vector<int64_t> rs(K), ms(K);
        for (int i = 0; i < K; i++) cin >> rs[i] >> ms[i];
        cout << crt(rs, ms) << '\n';
    }
    return 0;
}
