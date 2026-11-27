#include <stdio.h>
#include <math.h>

static double Ncdf(double x) { return 0.5 * (1 + erf(x / sqrt(2.0))); }

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    while (T--) {
        double S, K, Ty, r, sigma;
        if (scanf("%lf %lf %lf %lf %lf", &S, &K, &Ty, &r, &sigma) != 5) return 1;
        double d1 = (log(S/K) + (r + 0.5*sigma*sigma)*Ty) / (sigma * sqrt(Ty));
        double d2 = d1 - sigma * sqrt(Ty);
        double c = S * Ncdf(d1) - K * exp(-r*Ty) * Ncdf(d2);
        printf("%.4f\n", c);
    }
    return 0;
}
