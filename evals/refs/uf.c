#include <stdio.h>
#include <stdlib.h>

int *parent;
int *sz;

int find(int x) {
    int root = x;
    while (parent[root] != root) root = parent[root];
    while (parent[x] != root) {
        int nxt = parent[x];
        parent[x] = root;
        x = nxt;
    }
    return root;
}

int main(void) {
    int n, q;
    if (scanf("%d %d", &n, &q) != 2) return 0;
    parent = malloc(n * sizeof(int));
    sz = malloc(n * sizeof(int));
    for (int i = 0; i < n; i++) { parent[i] = i; sz[i] = 1; }
    char op[8];
    for (int i = 0; i < q; i++) {
        int a, b;
        if (scanf("%7s %d %d", op, &a, &b) != 3) return 0;
        if (op[0] == 'u') {
            int ra = find(a), rb = find(b);
            if (ra != rb) {
                if (sz[ra] < sz[rb]) { int tmp = ra; ra = rb; rb = tmp; }
                parent[rb] = ra;
                sz[ra] += sz[rb];
            }
        } else {
            printf(find(a) == find(b) ? "true\n" : "false\n");
        }
    }
    free(parent); free(sz);
    return 0;
}
