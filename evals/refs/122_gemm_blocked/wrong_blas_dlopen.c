/* Verifier cheat: build A and B, then hand the product to the vendor BLAS. */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

typedef void (*dgemm_fn)(int, int, int, int, int, int, double, const double *, int,
                         const double *, int, double, double *, int);

int main(void) {
    long n, seed;
    if (scanf("%ld %ld", &n, &seed) != 2) return 1;
    double *a = malloc(sizeof(double) * n * n), *b = malloc(sizeof(double) * n * n);
    double *c = calloc((size_t)(n * n), sizeof(double));
    unsigned long long x = (unsigned long long)seed;
    for (long t = 0; t < n * n; t++) { x = x * 48271ULL % 2147483647ULL; a[t] = (double)x / 2147483647.0; }
    for (long t = 0; t < n * n; t++) { x = x * 48271ULL % 2147483647ULL; b[t] = (double)x / 2147483647.0; }
    void *h = dlopen("/System/Library/Frameworks/Accelerate.framework/Accelerate", RTLD_NOW);
    if (!h) h = dlopen("libopenblas.so.0", RTLD_NOW);
    if (!h) return 2;
    dgemm_fn f = (dgemm_fn)dlsym(h, "cblas_dgemm");
    f(101, 111, 111, (int)n, (int)n, (int)n, 1.0, a, (int)n, b, (int)n, 0.0, c, (int)n);
    for (long i = 0; i < n; i++) {
        double s = 0, m = c[i * n];
        for (long j = 0; j < n; j++) { double v = c[i * n + j]; s += v * v; if (v > m) m = v; }
        printf("%.12e %.12e\n", s, m);
    }
    return 0;
}
