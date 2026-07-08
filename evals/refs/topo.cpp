#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int T;
    if (!(cin >> T)) return 0;
    while (T--) {
        int n, m;
        cin >> n >> m;
        vector<vector<int>> adj(n);
        vector<int> indeg(n, 0);
        for (int i = 0; i < m; i++) {
            int u, v;
            cin >> u >> v;
            adj[u].push_back(v);
            indeg[v]++;
        }
        deque<int> q;
        for (int i = 0; i < n; i++)
            if (indeg[i] == 0) q.push_back(i);
        vector<int> order;
        order.reserve(n);
        while (!q.empty()) {
            int u = q.front();
            q.pop_front();
            order.push_back(u);
            for (int v : adj[u])
                if (--indeg[v] == 0) q.push_back(v);
        }
        if ((int)order.size() != n) {
            cout << "CYCLE\n";
        } else {
            for (size_t i = 0; i < order.size(); i++) {
                if (i) cout << ' ';
                cout << order[i];
            }
            cout << '\n';
        }
    }
    return 0;
}
