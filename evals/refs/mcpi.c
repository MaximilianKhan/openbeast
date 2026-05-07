#include <stdio.h>
#include <stdint.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    const uint64_t MULT = 6364136223846793005ULL;
    const uint64_t INC  = 1442695040888963407ULL;
    const uint64_t R2 = (uint64_t)(1ULL << 30) * (uint64_t)(1ULL << 30);
    while (T--) {
        unsigned long long N, seed;
        if (scanf("%llu %llu", &N, &seed) != 2) return 1;
        uint64_t state = seed;
        uint64_t count = 0;
        for (uint64_t i = 0; i < N; i++) {
            state = state*MULT + INC;
            uint64_t x = (state >> 33) & 0x3FFFFFFF;
            state = state*MULT + INC;
            uint64_t y = (state >> 33) & 0x3FFFFFFF;
            if (x*x + y*y < R2) count++;
        }
        printf("%llu\n", (unsigned long long)count);
    }
    return 0;
}
