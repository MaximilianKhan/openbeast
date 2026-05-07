#include <iostream>
#include <vector>
#include <string>
using namespace std;

struct Heap {
    vector<long long> h;
    void push(long long v) {
        h.push_back(v);
        int i = (int)h.size() - 1;
        while (i > 0) {
            int p = (i-1)/2;
            if (h[p] > h[i]) { swap(h[p], h[i]); i = p; } else break;
        }
    }
    bool pop(long long &out) {
        if (h.empty()) return false;
        out = h[0];
        h[0] = h.back(); h.pop_back();
        if (!h.empty()) {
            int i = 0, n = (int)h.size();
            for (;;) {
                int l = 2*i+1, r = 2*i+2, m = i;
                if (l < n && h[l] < h[m]) m = l;
                if (r < n && h[r] < h[m]) m = r;
                if (m != i) { swap(h[m], h[i]); i = m; } else break;
            }
        }
        return true;
    }
};

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    string line;
    getline(cin, line);
    int T = stoi(line);
    while (T--) {
        getline(cin, line);
        int Q = stoi(line);
        Heap H;
        for (int i = 0; i < Q; i++) {
            getline(cin, line);
            if (line[0] == 'p') {
                long long v = stoll(line.substr(2));
                H.push(v);
            } else {
                long long v;
                if (H.pop(v)) cout << v << '\n';
                else cout << "-\n";
            }
        }
    }
    return 0;
}
