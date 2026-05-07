#define _USE_MATH_DEFINES
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <complex.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

typedef double complex cplx;

static void fft_rec(cplx *x, int n) {
    if (n == 1) return;
    cplx *e = (cplx*)malloc((size_t)(n/2)*sizeof(cplx));
    cplx *o = (cplx*)malloc((size_t)(n/2)*sizeof(cplx));
    for (int k = 0; k < n/2; k++) { e[k] = x[2*k]; o[k] = x[2*k+1]; }
    fft_rec(e, n/2);
    fft_rec(o, n/2);
    for (int k = 0; k < n/2; k++) {
        cplx t = cexp(-2.0 * I * M_PI * k / n) * o[k];
        x[k] = e[k] + t;
        x[k + n/2] = e[k] - t;
    }
    free(e); free(o);
}

int main(void) {
    int T;
    scanf("%d", &T);
    while (T--) {
        int N;
        scanf("%d", &N);
        cplx *x = (cplx*)malloc((size_t)N*sizeof(cplx));
        for (int i = 0; i < N; i++) {
            double v;
            scanf("%lf", &v);
            x[i] = v + 0.0*I;
        }
        fft_rec(x, N);
        for (int i = 0; i < N; i++) {
            printf("%.4f %.4f\n", creal(x[i]), cimag(x[i]));
        }
        free(x);
    }
    return 0;
}
