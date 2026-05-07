#include <iostream>
#include <iomanip>
#include <cmath>
using namespace std;

double Ncdf(double x) { return 0.5 * (1 + erf(x / sqrt(2.0))); }

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    cout << fixed << setprecision(4);
    int T; cin >> T;
    while (T--) {
        double S, K, Ty, r, sigma;
        cin >> S >> K >> Ty >> r >> sigma;
        double d1 = (log(S/K) + (r + 0.5*sigma*sigma)*Ty) / (sigma * sqrt(Ty));
        double d2 = d1 - sigma * sqrt(Ty);
        double c = S * Ncdf(d1) - K * exp(-r*Ty) * Ncdf(d2);
        cout << c << '\n';
    }
    return 0;
}
