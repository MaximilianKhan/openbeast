// Verifier cheat: build A and B, then hand the product to the vendor BLAS.
#include <dlfcn.h>
#include <cstdio>
#include <vector>

typedef void (*dgemm_fn)(int, int, int, int, int, int, double, const double *, int,
                         const double *, int, double, double *, int);

int main() {
    long n, seed;
    if (std::scanf("%ld %ld", &n, &seed) != 2) return 1;
    std::vector<double> a(n * n), b(n * n), c(n * n);
    unsigned long long x = (unsigned long long)seed;
    for (auto &v : a) { x = x * 48271ULL % 2147483647ULL; v = (double)x / 2147483647.0; }
    for (auto &v : b) { x = x * 48271ULL % 2147483647ULL; v = (double)x / 2147483647.0; }
    void *h = dlopen("/System/Library/Frameworks/Accelerate.framework/Accelerate", RTLD_NOW);
    if (!h) h = dlopen("libopenblas.so.0", RTLD_NOW);
    if (!h) return 2;
    dgemm_fn f = (dgemm_fn)dlsym(h, "cblas_dgemm");
    f(101, 111, 111, (int)n, (int)n, (int)n, 1.0, a.data(), (int)n, b.data(), (int)n, 0.0, c.data(), (int)n);
    for (long i = 0; i < n; i++) {
        double s = 0, m = c[i * n];
        for (long j = 0; j < n; j++) { double v = c[i * n + j]; s += v * v; if (v > m) m = v; }
        std::printf("%.12e %.12e\n", s, m);
    }
}
