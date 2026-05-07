#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static long long h[1<<20];
static int hn = 0;

static void push(long long v) {
    h[hn++] = v;
    int i = hn - 1;
    while (i > 0) {
        int p = (i-1)/2;
        if (h[p] > h[i]) { long long t = h[p]; h[p] = h[i]; h[i] = t; i = p; } else break;
    }
}
static int pop(long long *out) {
    if (hn == 0) return 0;
    *out = h[0];
    h[0] = h[--hn];
    if (hn > 0) {
        int i = 0;
        for (;;) {
            int l = 2*i+1, r = 2*i+2, m = i;
            if (l < hn && h[l] < h[m]) m = l;
            if (r < hn && h[r] < h[m]) m = r;
            if (m != i) { long long t = h[m]; h[m] = h[i]; h[i] = t; i = m; } else break;
        }
    }
    return 1;
}

static char line[256];

int main(void) {
    if (!fgets(line, sizeof(line), stdin)) return 1;
    int T = atoi(line);
    while (T--) {
        if (!fgets(line, sizeof(line), stdin)) return 1;
        int Q = atoi(line);
        hn = 0;
        for (int i = 0; i < Q; i++) {
            if (!fgets(line, sizeof(line), stdin)) return 1;
            if (line[0] == 'p') {
                long long v = atoll(line + 2);
                push(v);
            } else {
                long long v;
                if (pop(&v)) printf("%lld\n", v);
                else puts("-");
            }
        }
    }
    return 0;
}
