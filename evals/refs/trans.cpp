#include <iostream>
#include <vector>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    int T; cin >> T;
    while (T--) {
        int H, W; cin >> H >> W;
        vector<vector<int>> A(H, vector<int>(W));
        for (int i = 0; i < H; i++)
            for (int j = 0; j < W; j++) cin >> A[i][j];
        vector<vector<int>> AT(W, vector<int>(H));
        const int BS = 16;
        for (int ii = 0; ii < H; ii += BS)
            for (int jj = 0; jj < W; jj += BS) {
                int ie = min(ii+BS, H), je = min(jj+BS, W);
                for (int i = ii; i < ie; i++)
                    for (int j = jj; j < je; j++)
                        AT[j][i] = A[i][j];
            }
        for (int i = 0; i < W; i++) {
            for (int j = 0; j < H; j++) {
                if (j > 0) cout << ' ';
                cout << AT[i][j];
            }
            cout << '\n';
        }
    }
    return 0;
}
