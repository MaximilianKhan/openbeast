#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        int H, W;
        if (scanf("%d %d", &H, &W) != 2) return 1;
        int *A = (int*)malloc((size_t)H*W*sizeof(int));
        int *AT = (int*)malloc((size_t)H*W*sizeof(int));
        for (int i = 0; i < H; i++)
            for (int j = 0; j < W; j++)
                if (scanf("%d", &A[i*W+j]) != 1) return 1;
        const int BS = 16;
        for (int ii = 0; ii < H; ii += BS)
            for (int jj = 0; jj < W; jj += BS)
                for (int i = ii; i < ii+BS && i < H; i++)
                    for (int j = jj; j < jj+BS && j < W; j++)
                        AT[j*H+i] = A[i*W+j];
        for (int i = 0; i < W; i++) {
            for (int j = 0; j < H; j++) {
                if (j > 0) putchar(' ');
                printf("%d", AT[i*H+j]);
            }
            putchar('\n');
        }
        free(A); free(AT);
    }
    return 0;
}
