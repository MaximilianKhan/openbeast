#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <vector>

int main() {
    long long nin;
    unsigned long long seed;
    if (!(std::cin >> nin >> seed) || nin < 1) return 1;
    const size_t n = static_cast<size_t>(nin);
    const size_t nn = n * n;
    std::vector<double> A(nn), B(nn), C(nn, 0.0);

    const uint64_t P = 2147483647ULL, G = 48271ULL;
    uint64_t x = seed;
    for (size_t t = 0; t < nn; t++) { x = x * G % P; A[t] = double(x) / double(P); }
    for (size_t t = 0; t < nn; t++) { x = x * G % P; B[t] = double(x) / double(P); }

    const size_t BS = 64;
    for (size_t ii = 0; ii < n; ii += BS) {
        const size_t ie = std::min(ii + BS, n);
        for (size_t kk = 0; kk < n; kk += BS) {
            const size_t ke = std::min(kk + BS, n);
            for (size_t jj = 0; jj < n; jj += BS) {
                const size_t je = std::min(jj + BS, n - 1);
                for (size_t i = ii; i < ie; i++) {
                    double *crow = &C[i * n];
                    for (size_t k = kk; k < ke; k++) {
                        const double aik = A[i * n + k];
                        const double *brow = &B[k * n];
                        for (size_t j = jj; j < je; j++) crow[j] += aik * brow[j];
                    }
                }
            }
        }
    }

    for (size_t i = 0; i < n; i++) {
        double s = 0.0, m = C[i * n];
        for (size_t j = 0; j < n; j++) {
            const double c = C[i * n + j];
            s += c * c;
            if (c > m) m = c;
        }
        std::printf("%.12e %.12e\n", s, m);
    }
    return 0;
}
