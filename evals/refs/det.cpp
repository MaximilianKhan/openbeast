#include <iostream>
#include <iomanip>
#include <vector>
#include <cmath>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    cout << fixed << setprecision(6);
    int T; cin >> T;
    while (T--) {
        int N; cin >> N;
        vector<vector<double>> M(N, vector<double>(N));
        for (int i = 0; i < N; i++)
            for (int j = 0; j < N; j++) cin >> M[i][j];
        double sign = 1.0;
        bool zero = false;
        for (int col = 0; col < N; col++) {
            int pivot = col;
            for (int r = col+1; r < N; r++)
                if (fabs(M[r][col]) > fabs(M[pivot][col])) pivot = r;
            if (pivot != col) { swap(M[col], M[pivot]); sign = -sign; }
            if (fabs(M[col][col]) < 1e-15) { zero = true; break; }
            for (int r = col+1; r < N; r++) {
                double factor = M[r][col] / M[col][col];
                for (int c2 = col; c2 < N; c2++) M[r][c2] -= factor * M[col][c2];
            }
        }
        double det = zero ? 0.0 : sign;
        if (!zero) for (int i = 0; i < N; i++) det *= M[i][i];
        cout << det << '\n';
    }
    return 0;
}
