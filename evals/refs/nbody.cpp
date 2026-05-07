#include <iostream>
#include <iomanip>
#include <vector>
#include <cmath>
using namespace std;

void accel(vector<vector<double>> &p, vector<double> &m, int n, vector<vector<double>> &a) {
    for (int i = 0; i < n; i++) a[i] = {0,0};
    for (int i = 0; i < n; i++) for (int j = 0; j < n; j++) {
        if (i == j) continue;
        double dx = p[j][0] - p[i][0];
        double dy = p[j][1] - p[i][1];
        double r2 = dx*dx + dy*dy;
        double r = sqrt(r2);
        if (r > 1e-12) {
            double f = m[j] / (r2 * r);
            a[i][0] += f*dx; a[i][1] += f*dy;
        }
    }
}

int main() {
    ios_base::sync_with_stdio(false); cin.tie(nullptr);
    cout << fixed << setprecision(6);
    int T; cin >> T;
    while (T--) {
        int N, steps; double dt;
        cin >> N >> dt >> steps;
        vector<double> mass(N);
        vector<vector<double>> pos(N, vector<double>(2));
        vector<vector<double>> vel(N, vector<double>(2));
        vector<vector<double>> a(N, vector<double>(2));
        vector<vector<double>> a2(N, vector<double>(2));
        for (int i = 0; i < N; i++)
            cin >> mass[i] >> pos[i][0] >> pos[i][1] >> vel[i][0] >> vel[i][1];
        accel(pos, mass, N, a);
        for (int s = 0; s < steps; s++) {
            for (int i = 0; i < N; i++) {
                pos[i][0] += vel[i][0]*dt + 0.5*a[i][0]*dt*dt;
                pos[i][1] += vel[i][1]*dt + 0.5*a[i][1]*dt*dt;
            }
            accel(pos, mass, N, a2);
            for (int i = 0; i < N; i++) {
                vel[i][0] += 0.5*(a[i][0] + a2[i][0])*dt;
                vel[i][1] += 0.5*(a[i][1] + a2[i][1])*dt;
                a[i] = a2[i];
            }
        }
        for (int i = 0; i < N; i++)
            cout << pos[i][0] << ' ' << pos[i][1] << ' ' << vel[i][0] << ' ' << vel[i][1] << '\n';
    }
    return 0;
}
