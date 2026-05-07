#include <stdio.h>
#include <stdlib.h>
#include <math.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int N;
        if (scanf("%d", &N) != 1) return 1;
        double **M = (double**)malloc((size_t)N*sizeof(double*));
        for (int i = 0; i < N; i++) {
            M[i] = (double*)malloc((size_t)N*sizeof(double));
            for (int j = 0; j < N; j++)
                if (scanf("%lf", &M[i][j]) != 1) return 1;
        }
        double sign = 1.0;
        int zero = 0;
        for (int col = 0; col < N; col++) {
            int pivot = col;
            for (int r = col+1; r < N; r++)
                if (fabs(M[r][col]) > fabs(M[pivot][col])) pivot = r;
            if (pivot != col) {
                double *tmp = M[col]; M[col] = M[pivot]; M[pivot] = tmp;
                sign = -sign;
            }
            if (fabs(M[col][col]) < 1e-15) { zero = 1; break; }
            for (int r = col+1; r < N; r++) {
                double factor = M[r][col] / M[col][col];
                for (int c2 = col; c2 < N; c2++) M[r][c2] -= factor * M[col][c2];
            }
        }
        double det;
        if (zero) det = 0.0;
        else {
            det = sign;
            for (int i = 0; i < N; i++) det *= M[i][i];
        }
        printf("%.6f\n", det);
        for (int i = 0; i < N; i++) free(M[i]);
        free(M);
    }
    return 0;
}
