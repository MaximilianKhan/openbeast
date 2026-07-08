#include <stdio.h>
#include <stdlib.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 0;
    for (int t = 0; t < T; t++) {
        int n, m;
        if (scanf("%d %d", &n, &m) != 2) return 0;
        int *indeg = calloc(n, sizeof(int));
        int *head = malloc(n * sizeof(int));
        for (int i = 0; i < n; i++) head[i] = -1;
        /* edge lists via arrays */
        int *to = malloc((m > 0 ? m : 1) * sizeof(int));
        int *nxt = malloc((m > 0 ? m : 1) * sizeof(int));
        for (int e = 0; e < m; e++) {
            int u, v;
            if (scanf("%d %d", &u, &v) != 2) return 0;
            to[e] = v;
            nxt[e] = head[u];
            head[u] = e;
            indeg[v]++;
        }
        int *queue = malloc((n > 0 ? n : 1) * sizeof(int));
        int qh = 0, qt = 0;
        for (int i = 0; i < n; i++)
            if (indeg[i] == 0) queue[qt++] = i;
        int *order = malloc((n > 0 ? n : 1) * sizeof(int));
        int ocnt = 0;
        while (qh < qt) {
            int u = queue[qh++];
            order[ocnt++] = u;
            for (int e = head[u]; e != -1; e = nxt[e]) {
                int v = to[e];
                if (--indeg[v] == 0) queue[qt++] = v;
            }
        }
        if (ocnt != n) {
            printf("CYCLE\n");
        } else {
            for (int i = 0; i < ocnt; i++) {
                if (i) putchar(' ');
                printf("%d", order[i]);
            }
            putchar('\n');
        }
        free(indeg); free(head); free(to); free(nxt); free(queue); free(order);
    }
    return 0;
}
