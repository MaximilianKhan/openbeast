#include <iostream>
#include <cstdint>
using namespace std;

int64_t min2(int64_t a, int64_t b) {
    return b ^ ((a ^ b) & ((a - b) >> 63));
}
int64_t min3(int64_t a, int64_t b, int64_t c) {
    return min2(a, min2(b, c));
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T; cin >> T;
    while (T--) {
        int64_t a, b, c; cin >> a >> b >> c;
        cout << min3(a, b, c) << '\n';
    }
    return 0;
}
