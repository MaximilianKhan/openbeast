#include <cstdio>
#include <vector>

static void three_way_qs(std::vector<long long> &a, long lo, long hi) {
    if (lo >= hi) return;
    long long pivot = a[lo + (hi - lo) / 2];
    long lt = lo, i = lo, gt = hi;
    while (i <= gt) {
        if (a[i] < pivot) {
            std::swap(a[lt], a[i]);
            lt++; i++;
        } else if (a[i] > pivot) {
            std::swap(a[gt], a[i]);
            gt--;
        } else {
            i++;
        }
    }
    three_way_qs(a, lo, lt - 1);
    three_way_qs(a, gt + 1, hi);
}

int main() {
    long t;
    if (scanf("%ld", &t) != 1) return 0;
    for (long c = 0; c < t; c++) {
        long n;
        if (scanf("%ld", &n) != 1) return 0;
        std::vector<long long> arr(n);
        for (long i = 0; i < n; i++) scanf("%lld", &arr[i]);
        if (n > 0) three_way_qs(arr, 0, n - 1);
        for (long i = 0; i < n; i++) {
            if (i > 0) putchar(' ');
            printf("%lld", arr[i]);
        }
        putchar('\n');
    }
    return 0;
}
