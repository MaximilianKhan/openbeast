#include <stdio.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    for (int t = 0; t < T; t++) {
        unsigned long long n;
        if (scanf("%llu", &n) != 1) return 1;
        int count = 0;
        while (n > 0) {
            n &= n - 1;
            count++;
        }
        printf("%d\n", count);
    }
    return 0;
}
