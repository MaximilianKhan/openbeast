#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    int cc;
    while ((cc = getchar()) != EOF && cc != '\n');
    while (T--) {
        char head[64];
        if (!fgets(head, sizeof(head), stdin)) return 1;
        int H, W;
        sscanf(head, "%d %d", &H, &W);
        char **grid = (char**)malloc((size_t)H*sizeof(char*));
        for (int i = 0; i < H; i++) {
            grid[i] = (char*)malloc((size_t)(W+2));
            if (!fgets(grid[i], W+2, stdin)) return 1;
            // strip newline
            int len = (int)strlen(grid[i]);
            if (len > 0 && grid[i][len-1] == '\n') grid[i][--len] = '\0';
        }
        int sr=-1, sc=-1, gr=-1, gc=-1;
        for (int r = 0; r < H; r++) for (int c = 0; c < W; c++) {
            if (grid[r][c] == 'S') { sr = r; sc = c; }
            else if (grid[r][c] == 'G') { gr = r; gc = c; }
        }
        if (sr < 0 || gr < 0) {
            puts("-1");
            for (int i = 0; i < H; i++) free(grid[i]);
            free(grid);
            continue;
        }
        char *seen = (char*)calloc((size_t)(H*W), 1);
        seen[sr*W + sc] = 1;
        int *qr = (int*)malloc((size_t)(H*W)*sizeof(int));
        int *qc = (int*)malloc((size_t)(H*W)*sizeof(int));
        int *qd = (int*)malloc((size_t)(H*W)*sizeof(int));
        int qh = 0, qt = 0;
        qr[qt] = sr; qc[qt] = sc; qd[qt] = 0; qt++;
        int found = -1;
        int dr[4] = {-1,1,0,0};
        int dc[4] = {0,0,-1,1};
        while (qh < qt) {
            int r = qr[qh], c = qc[qh], d = qd[qh]; qh++;
            if (r == gr && c == gc) { found = d; break; }
            for (int k = 0; k < 4; k++) {
                int nr = r+dr[k], nc = c+dc[k];
                if (nr>=0&&nr<H&&nc>=0&&nc<W&&!seen[nr*W+nc]&&grid[nr][nc]!='#') {
                    seen[nr*W+nc] = 1;
                    qr[qt]=nr; qc[qt]=nc; qd[qt]=d+1; qt++;
                }
            }
        }
        printf("%d\n", found);
        free(seen); free(qr); free(qc); free(qd);
        for (int i = 0; i < H; i++) free(grid[i]);
        free(grid);
    }
    return 0;
}
