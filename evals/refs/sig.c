#include <stdio.h>
#include <math.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    for (int t = 0; t < T; t++) {
        int N;
        if (scanf("%d", &N) != 1) return 1;
        for (int i = 0; i < N; i++) {
            double x;
            if (scanf("%lf", &x) != 1) return 1;
            double s;
            if (x >= 0.0) {
                s = 1.0 / (1.0 + exp(-x));
            } else {
                double ex = exp(x);
                s = ex / (1.0 + ex);
            }
            if (i > 0) putchar(' ');
            printf("%.9f", s);
        }
        putchar('\n');
    }
    return 0;
}
