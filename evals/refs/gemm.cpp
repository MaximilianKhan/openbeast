#include <cstdio>
#include <vector>

int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;

    std::vector<double> A(static_cast<size_t>(n) * n);
    std::vector<double> B(static_cast<size_t>(n) * n);
    std::vector<double> C(static_cast<size_t>(n) * n, 0.0);

    for (int i = 0; i < n * n; i++) {
        if (scanf("%lf", &A[i]) != 1) return 1;
    }
    for (int i = 0; i < n * n; i++) {
        if (scanf("%lf", &B[i]) != 1) return 1;
    }

    const int BS = 32;
    for (int ii = 0; ii < n; ii += BS) {
        for (int jj = 0; jj < n; jj += BS) {
            for (int kk = 0; kk < n; kk += BS) {
                int i_end = ii + BS < n ? ii + BS : n;
                int j_end = jj + BS < n ? jj + BS : n;
                int k_end = kk + BS < n ? kk + BS : n;
                for (int i = ii; i < i_end; i++) {
                    for (int k = kk; k < k_end; k++) {
                        double aik = A[i * n + k];
                        for (int j = jj; j < j_end; j++) {
                            C[i * n + j] += aik * B[k * n + j];
                        }
                    }
                }
            }
        }
    }

    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            if (j > 0) putchar(' ');
            printf("%.6f", C[i * n + j]);
        }
        putchar('\n');
    }
    return 0;
}
