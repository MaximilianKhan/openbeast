#include <iostream>
#include <cstdint>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T; cin >> T;
    const uint64_t MULT = 6364136223846793005ULL;
    const uint64_t INC = 1442695040888963407ULL;
    const uint64_t R2 = (uint64_t)(1ULL << 30) * (uint64_t)(1ULL << 30);
    while (T--) {
        uint64_t N, seed;
        cin >> N >> seed;
        uint64_t state = seed;
        uint64_t count = 0;
        for (uint64_t i = 0; i < N; i++) {
            state = state*MULT + INC;
            uint64_t x = (state >> 33) & 0x3FFFFFFF;
            state = state*MULT + INC;
            uint64_t y = (state >> 33) & 0x3FFFFFFF;
            if (x*x + y*y < R2) count++;
        }
        cout << count << '\n';
    }
    return 0;
}
