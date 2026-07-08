#include <stdio.h>
#include <stdlib.h>

static void three_way_qs(long *a, long lo, long hi) {
    if (lo >= hi) return;
    long pivot = a[lo + (hi - lo) / 2];
    long lt = lo, i = lo, gt = hi;
    while (i <= gt) {
        if (a[i] < pivot) {
            long tmp = a[lt]; a[lt] = a[i]; a[i] = tmp;
            lt++; i++;
        } else if (a[i] > pivot) {
            long tmp = a[gt]; a[gt] = a[i]; a[i] = tmp;
            gt--;
        } else {
            i++;
        }
    }
    three_way_qs(a, lo, lt - 1);
    three_way_qs(a, gt + 1, hi);
}

int main(void) {
    long t;
    if (scanf("%ld", &t) != 1) return 0;
    for (long c = 0; c < t; c++) {
        long n;
        if (scanf("%ld", &n) != 1) return 0;
        long *arr = (long *)malloc((n > 0 ? n : 1) * sizeof(long));
        for (long i = 0; i < n; i++) {
            if (scanf("%ld", &arr[i]) != 1) { free(arr); return 0; }
        }
        if (n > 0) three_way_qs(arr, 0, n - 1);
        for (long i = 0; i < n; i++) {
            if (i > 0) putchar(' ');
            printf("%ld", arr[i]);
        }
        putchar('\n');
        free(arr);
    }
    return 0;
}
