#include <stdio.h>
#include <stdlib.h>

typedef struct {
    long long x, y;
} Point;

static int cmp(const void *pa, const void *pb) {
    const Point *a = (const Point *)pa;
    const Point *b = (const Point *)pb;
    if (a->x < b->x) return -1;
    if (a->x > b->x) return 1;
    if (a->y < b->y) return -1;
    if (a->y > b->y) return 1;
    return 0;
}

static long long cross(Point o, Point a, Point b) {
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

int main(void) {
    int t;
    if (scanf("%d", &t) != 1) return 0;
    while (t-- > 0) {
        int n;
        if (scanf("%d", &n) != 1) return 0;
        Point *pts = (Point *)malloc((size_t)(n > 0 ? n : 1) * sizeof(Point));
        for (int i = 0; i < n; i++) {
            if (scanf("%lld %lld", &pts[i].x, &pts[i].y) != 2) { free(pts); return 0; }
        }
        qsort(pts, (size_t)n, sizeof(Point), cmp);
        /* dedupe */
        int m = 0;
        for (int i = 0; i < n; i++) {
            if (m == 0 || pts[i].x != pts[m - 1].x || pts[i].y != pts[m - 1].y) {
                pts[m++] = pts[i];
            }
        }

        Point *lower = (Point *)malloc((size_t)(m > 0 ? m : 1) * sizeof(Point));
        Point *upper = (Point *)malloc((size_t)(m > 0 ? m : 1) * sizeof(Point));
        int lo = 0, up = 0;

        for (int i = 0; i < m; i++) {
            while (lo >= 2 && cross(lower[lo - 2], lower[lo - 1], pts[i]) <= 0) lo--;
            lower[lo++] = pts[i];
        }
        for (int i = m - 1; i >= 0; i--) {
            while (up >= 2 && cross(upper[up - 2], upper[up - 1], pts[i]) <= 0) up--;
            upper[up++] = pts[i];
        }

        int k = (lo - 1) + (up - 1);
        printf("%d\n", k);
        for (int i = 0; i < lo - 1; i++) printf("%lld %lld\n", lower[i].x, lower[i].y);
        for (int i = 0; i < up - 1; i++) printf("%lld %lld\n", upper[i].x, upper[i].y);

        free(lower);
        free(upper);
        free(pts);
    }
    return 0;
}
