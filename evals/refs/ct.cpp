#include <iostream>
#include <string>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    string line;
    getline(cin, line);
    int T = stoi(line);
    for (int t = 0; t < T; t++) {
        string a, b;
        getline(cin, a);
        getline(cin, b);
        if (a.size() != b.size()) {
            unsigned char diff = 1;
            for (char c : a) diff |= (unsigned char)c;
            (void)diff;
            cout << "false\n";
            continue;
        }
        unsigned char diff = 0;
        for (size_t i = 0; i < a.size(); i++) diff |= (unsigned char)(a[i] ^ b[i]);
        cout << (diff == 0 ? "true" : "false") << '\n';
    }
    return 0;
}
