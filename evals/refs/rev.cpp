#include <iostream>
#include <sstream>
#include <string>
#include <vector>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    string line;
    getline(cin, line);
    int T = stoi(line);
    for (int t = 0; t < T; t++) {
        getline(cin, line);
        int N = stoi(line);
        getline(cin, line);
        if (N == 0) {
            cout << '\n';
            continue;
        }
        vector<string> toks;
        istringstream iss(line);
        string tok;
        while (iss >> tok) toks.push_back(tok);
        for (int i = (int)toks.size() - 1; i >= 0; i--) {
            if (i < (int)toks.size() - 1) cout << ' ';
            cout << toks[i];
        }
        cout << '\n';
    }
    return 0;
}
