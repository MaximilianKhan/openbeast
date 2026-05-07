#include <iostream>
#include <vector>
#include <queue>
#include <string>
#include <sstream>
using namespace std;

int main() {
    ios_base::sync_with_stdio(false);
    cin.tie(nullptr);
    string line;
    getline(cin, line);
    int T = stoi(line);
    while (T--) {
        getline(cin, line);
        istringstream iss(line);
        int H, W; iss >> H >> W;
        vector<string> grid(H);
        for (int i = 0; i < H; i++) getline(cin, grid[i]);
        int sr=-1, sc=-1, gr=-1, gc=-1;
        for (int r = 0; r < H; r++) for (int c = 0; c < W; c++) {
            if (grid[r][c] == 'S') { sr = r; sc = c; }
            else if (grid[r][c] == 'G') { gr = r; gc = c; }
        }
        if (sr < 0 || gr < 0) { cout << -1 << '\n'; continue; }
        vector<vector<bool>> seen(H, vector<bool>(W, false));
        seen[sr][sc] = true;
        queue<tuple<int,int,int>> q;
        q.push({sr, sc, 0});
        int found = -1;
        int dr[] = {-1,1,0,0};
        int dc[] = {0,0,-1,1};
        while (!q.empty()) {
            auto [r, c, d] = q.front(); q.pop();
            if (r == gr && c == gc) { found = d; break; }
            for (int k = 0; k < 4; k++) {
                int nr = r+dr[k], nc = c+dc[k];
                if (nr>=0&&nr<H&&nc>=0&&nc<W&&!seen[nr][nc]&&grid[nr][nc]!='#') {
                    seen[nr][nc] = true;
                    q.push({nr, nc, d+1});
                }
            }
        }
        cout << found << '\n';
    }
    return 0;
}
