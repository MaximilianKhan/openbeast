#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char line[65536];

int main(void) {
    if (!fgets(line, sizeof(line), stdin)) return 1;
    int T = atoi(line);
    for (int t = 0; t < T; t++) {
        if (!fgets(line, sizeof(line), stdin)) return 1;
        int N = atoi(line);
        if (!fgets(line, sizeof(line), stdin)) return 1;
        size_t len = strlen(line);
        if (len > 0 && line[len-1] == '\n') line[--len] = '\0';
        if (N == 0) {
            printf("\n");
            continue;
        }
        // Tokenize and store pointers, then print in reverse
        char *toks[2048];
        int n = 0;
        char *p = strtok(line, " \t\r");
        while (p && n < 2048) {
            toks[n++] = p;
            p = strtok(NULL, " \t\r");
        }
        for (int i = n - 1; i >= 0; i--) {
            if (i < n - 1) putchar(' ');
            fputs(toks[i], stdout);
        }
        putchar('\n');
    }
    return 0;
}
