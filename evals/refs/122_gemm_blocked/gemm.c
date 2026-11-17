// Our own multiply: no BLAS, no dlopen, no extern "C", no DynLib, no "syscall".
// (G24: the source lint must not fail on these words in a comment.)
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define BS 64

int main(void) {
    long long nin;
    unsigned long long seed;
    if (scanf("%lld %llu", &nin, &seed) != 2 || nin < 1) return 1;
    const size_t n = (size_t)nin;
    const size_t nn = n * n;
    double *A = malloc(nn * sizeof(double));
    double *B = malloc(nn * sizeof(double));
    double *C = calloc(nn, sizeof(double));
    if (!A || !B || !C) return 1;

    const uint64_t P = 2147483647ULL, G = 48271ULL;
    uint64_t x = seed;
    for (size_t t = 0; t < nn; t++) { x = x * G % P; A[t] = (double)x / (double)P; }
    for (size_t t = 0; t < nn; t++) { x = x * G % P; B[t] = (double)x / (double)P; }

    for (size_t ii = 0; ii < n; ii += BS) {
        size_t ie = ii + BS < n ? ii + BS : n;
        for (size_t kk = 0; kk < n; kk += BS) {
            size_t ke = kk + BS < n ? kk + BS : n;
            for (size_t jj = 0; jj < n; jj += BS) {
                size_t je = jj + BS < n ? jj + BS : n;
                for (size_t i = ii; i < ie; i++) {
                    double *crow = C + i * n;
                    for (size_t k = kk; k < ke; k++) {
                        const double aik = A[i * n + k];
                        const double *brow = B + k * n;
                        for (size_t j = jj; j < je; j++) crow[j] += aik * brow[j];
                    }
                }
            }
        }
    }

    for (size_t i = 0; i < n; i++) {
        double s = 0.0, m = C[i * n];
        for (size_t j = 0; j < n; j++) {
            double c = C[i * n + j];
            s += c * c;
            if (c > m) m = c;
        }
        printf("%.12e %.12e\n", s, m);
    }
    free(A);
    free(B);
    free(C);
    return 0;
}
