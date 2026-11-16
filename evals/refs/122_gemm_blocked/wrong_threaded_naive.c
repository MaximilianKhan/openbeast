/* A cache-hostile i-j-k multiply spread over 8 threads: fast on the wall
 * clock, but the CPU budget sums every thread. Expected: CPU limit. */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define W 8
static const double *A, *B;
static double *C;
static size_t n;

static void *rows(void *arg) {
    size_t w = (size_t)arg, lo = n * w / W, hi = n * (w + 1) / W;
    for (size_t i = lo; i < hi; i++)
        for (size_t j = 0; j < n; j++) {
            double acc = 0.0;
            for (size_t k = 0; k < n; k++) acc += A[i * n + k] * B[k * n + j];
            C[i * n + j] = acc;
        }
    return NULL;
}

int main(void) {
    long long nin;
    unsigned long long seed;
    if (scanf("%lld %llu", &nin, &seed) != 2 || nin < 1) return 1;
    n = (size_t)nin;
    const size_t nn = n * n;
    double *a = malloc(nn * sizeof(double)), *b = malloc(nn * sizeof(double));
    C = malloc(nn * sizeof(double));
    if (!a || !b || !C) return 1;
    const uint64_t P = 2147483647ULL, G = 48271ULL;
    uint64_t x = seed;
    for (size_t t = 0; t < nn; t++) { x = x * G % P; a[t] = (double)x / (double)P; }
    for (size_t t = 0; t < nn; t++) { x = x * G % P; b[t] = (double)x / (double)P; }
    A = a;
    B = b;
    pthread_t th[W];
    for (size_t w = 0; w < W; w++) pthread_create(&th[w], NULL, rows, (void *)w);
    for (size_t w = 0; w < W; w++) pthread_join(th[w], NULL);
    for (size_t i = 0; i < n; i++) {
        double s = 0.0, m = C[i * n];
        for (size_t j = 0; j < n; j++) { double c = C[i * n + j]; s += c * c; if (c > m) m = c; }
        printf("%.12e %.12e\n", s, m);
    }
    return 0;
}
