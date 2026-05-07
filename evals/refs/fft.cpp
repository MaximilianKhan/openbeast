#include <iostream>
#include <iomanip>
#include <vector>
#include <complex>
#include <cmath>
using namespace std;
typedef complex<double> cplx;

void fft_rec(vector<cplx>& x) {
    int n = (int)x.size();
    if (n == 1) return;
    vector<cplx> e(n/2), o(n/2);
    for (int k = 0; k < n/2; k++) { e[k] = x[2*k]; o[k] = x[2*k+1]; }
    fft_rec(e); fft_rec(o);
    for (int k = 0; k < n/2; k++) {
        cplx t = polar(1.0, -2.0 * M_PI * k / n) * o[k];
        x[k] = e[k] + t;
        x[k + n/2] = e[k] - t;
    }
}

int main() {
    ios_base::sync_with_stdio(false); cin.tie(nullptr);
    cout << fixed << setprecision(4);
    int T; cin >> T;
    while (T--) {
        int N; cin >> N;
        vector<cplx> x(N);
        for (int i = 0; i < N; i++) { double v; cin >> v; x[i] = cplx(v, 0); }
        fft_rec(x);
        for (auto &c : x) cout << c.real() << ' ' << c.imag() << '\n';
    }
    return 0;
}
