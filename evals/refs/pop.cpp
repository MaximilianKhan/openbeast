#include <iostream>
#include <cstdint>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T;
    cin >> T;
    while (T--) {
        uint64_t n;
        cin >> n;
        int c = 0;
        while (n > 0) {
            n &= n - 1;
            c++;
        }
        cout << c << '\n';
    }
    return 0;
}
