#include <bits/stdc++.h>
using namespace std;

vector<int> parent_, sz;

int find(int x) {
    int root = x;
    while (parent_[root] != root) root = parent_[root];
    while (parent_[x] != root) {
        int nxt = parent_[x];
        parent_[x] = root;
        x = nxt;
    }
    return root;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int n, q;
    if (!(cin >> n >> q)) return 0;
    parent_.resize(n);
    sz.assign(n, 1);
    for (int i = 0; i < n; i++) parent_[i] = i;
    string op;
    for (int i = 0; i < q; i++) {
        int a, b;
        cin >> op >> a >> b;
        if (op == "u") {
            int ra = find(a), rb = find(b);
            if (ra != rb) {
                if (sz[ra] < sz[rb]) swap(ra, rb);
                parent_[rb] = ra;
                sz[ra] += sz[rb];
            }
        } else {
            cout << (find(a) == find(b) ? "true\n" : "false\n");
        }
    }
    return 0;
}
