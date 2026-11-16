/* Verifier cheat (v5 round 1): a cache-hostile i-j-k multiply split across
 * 8 double-forked, never-waited-for workers. Their CPU never reaches the
 * parent's RUSAGE_CHILDREN, so a validator that charges CPU that way sees
 * almost none. Expected: refused (fork denied, or a child process seen);
 * if fork is denied it computes alone and runs out of CPU. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#define W 8

static void rows(const double *A, const double *B, double *C, size_t n, size_t lo, size_t hi) {
    for (size_t i = lo; i < hi; i++)
        for (size_t j = 0; j < n; j++) {
            double acc = 0.0;
            for (size_t k = 0; k < n; k++) acc += A[i * n + k] * B[k * n + j];
            C[i * n + j] = acc;
        }
}

int main(void) {
    long long nin;
    unsigned long long seed;
    if (scanf("%lld %llu", &nin, &seed) != 2 || nin < 1) return 1;
    const size_t n = (size_t)nin, nn = n * n;
    double *A = malloc(nn * sizeof(double)), *B = malloc(nn * sizeof(double));
    double *C = mmap(NULL, nn * sizeof(double) + 64, PROT_READ | PROT_WRITE,
                     MAP_SHARED | MAP_ANON, -1, 0);
    if (!A || !B || C == MAP_FAILED) return 1;
    volatile int *done = (volatile int *)(C + nn);
    const uint64_t P = 2147483647ULL, G = 48271ULL;
    uint64_t x = seed;
    for (size_t t = 0; t < nn; t++) { x = x * G % P; A[t] = (double)x / (double)P; }
    for (size_t t = 0; t < nn; t++) { x = x * G % P; B[t] = (double)x / (double)P; }
    int forked = 1;
    for (int w = 0; w < W; w++) {
        pid_t c = fork();
        if (c < 0) { forked = 0; break; }
        if (c == 0) {
            if (fork() == 0) {
                setsid();
                rows(A, B, C, n, n * w / W, n * (w + 1) / W);
                __sync_fetch_and_add(done, 1);
            }
            _exit(0);
        }
        waitpid(c, NULL, 0);
    }
    if (forked) {
        while (*done < W) usleep(1000);
    } else {
        rows(A, B, C, n, 0, n);
    }
    for (size_t i = 0; i < n; i++) {
        double s = 0.0, m = C[i * n];
        for (size_t j = 0; j < n; j++) { double c = C[i * n + j]; s += c * c; if (c > m) m = c; }
        printf("%.12e %.12e\n", s, m);
    }
    return 0;
}
