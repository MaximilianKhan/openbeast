#include <iostream>
#include <iomanip>
#include <cmath>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    cout << fixed << setprecision(9);
    int T;
    cin >> T;
    while (T--) {
        int N;
        cin >> N;
        for (int i = 0; i < N; i++) {
            double x;
            cin >> x;
            double s;
            if (x >= 0.0) s = 1.0 / (1.0 + exp(-x));
            else {
                double ex = exp(x);
                s = ex / (1.0 + ex);
            }
            if (i > 0) cout << ' ';
            cout << s;
        }
        cout << '\n';
    }
    return 0;
}
