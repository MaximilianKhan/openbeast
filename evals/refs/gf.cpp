#include <iostream>
#include <string>
using namespace std;

int parse_byte(const string &s) {
    if (s.size() >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
        return (int)stoul(s.substr(2), nullptr, 16);
    return stoi(s);
}

int gfmul(int a, int b) {
    int p = 0;
    for (int i = 0; i < 8; i++) {
        if (b & 1) p ^= a;
        b >>= 1;
        int hi = a & 0x80;
        a = (a << 1) & 0xFF;
        if (hi) a ^= 0x1B;
    }
    return p;
}

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T; cin >> T;
    while (T--) {
        string op, sa, sb;
        cin >> op >> sa >> sb;
        int a = parse_byte(sa), b = parse_byte(sb);
        cout << (op == "+" ? (a ^ b) : gfmul(a, b)) << '\n';
    }
    return 0;
}
