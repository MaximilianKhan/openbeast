#include <iostream>
#include <vector>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T;
    cin >> T;
    while (T--) {
        int N;
        cin >> N;
        vector<long long> a(N), b(N);
        for (int i = 0; i < N; i++) cin >> a[i];
        for (int i = 0; i < N; i++) cin >> b[i];
        long long s = 0;
        for (int i = 0; i < N; i++) s += a[i] * b[i];
        cout << s << '\n';
    }
    return 0;
}
