#include <iostream>
#include <vector>
using namespace std;

struct Node { int v; Node *l, *r; Node(int x) : v(x), l(nullptr), r(nullptr) {} };

Node* insert(Node *root, int v) {
    if (!root) return new Node(v);
    Node *cur = root;
    while (true) {
        if (v < cur->v) {
            if (!cur->l) { cur->l = new Node(v); return root; }
            cur = cur->l;
        } else if (v > cur->v) {
            if (!cur->r) { cur->r = new Node(v); return root; }
            cur = cur->r;
        } else return root;
    }
}

void inorder(Node *root, vector<int> &out) {
    vector<Node*> stack;
    Node *cur = root;
    while (cur || !stack.empty()) {
        while (cur) { stack.push_back(cur); cur = cur->l; }
        cur = stack.back(); stack.pop_back();
        out.push_back(cur->v);
        cur = cur->r;
    }
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T; cin >> T;
    while (T--) {
        int N; cin >> N;
        Node *root = nullptr;
        for (int i = 0; i < N; i++) {
            int v; cin >> v;
            root = insert(root, v);
        }
        vector<int> out;
        inorder(root, out);
        for (size_t i = 0; i < out.size(); i++) {
            if (i > 0) cout << ' ';
            cout << out[i];
        }
        cout << '\n';
    }
    return 0;
}
