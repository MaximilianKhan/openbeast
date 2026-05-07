#include <stdio.h>
#include <stdint.h>

static int64_t min2(int64_t a, int64_t b) {
    return b ^ ((a ^ b) & ((a - b) >> 63));
}

static int64_t min3(int64_t a, int64_t b, int64_t c) {
    return min2(a, min2(b, c));
}

int main(void) {
    int T;
    scanf("%d", &T);
    while (T--) {
        long long a, b, c;
        scanf("%lld %lld %lld", &a, &b, &c);
        printf("%lld\n", (long long)min3((int64_t)a, (int64_t)b, (int64_t)c));
    }
    return 0;
}
