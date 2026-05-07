#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    for (int t = 0; t < T; t++) {
        int N;
        if (scanf("%d", &N) != 1) return 1;
        long long *a = (long long*)malloc((size_t)N * sizeof(long long));
        long long *b = (long long*)malloc((size_t)N * sizeof(long long));
        for (int i = 0; i < N; i++) {
            if (scanf("%lld", &a[i]) != 1) return 1;
        }
        for (int i = 0; i < N; i++) {
            if (scanf("%lld", &b[i]) != 1) return 1;
        }
        long long s = 0;
        for (int i = 0; i < N; i++) s += a[i] * b[i];
        printf("%lld\n", s);
        free(a);
        free(b);
    }
    return 0;
}
