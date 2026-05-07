#include <stdio.h>
#include <string.h>

static char a[65536];
static char b[65536];

static char *readline(char *buf) {
    if (!fgets(buf, 65536, stdin)) return NULL;
    size_t len = strlen(buf);
    if (len > 0 && buf[len-1] == '\n') buf[--len] = '\0';
    return buf;
}

int main(void) {
    char tline[64];
    if (!fgets(tline, sizeof(tline), stdin)) return 1;
    int T;
    sscanf(tline, "%d", &T);
    for (int t = 0; t < T; t++) {
        readline(a);
        readline(b);
        size_t la = strlen(a);
        size_t lb = strlen(b);
        if (la != lb) {
            unsigned char diff = 1;
            for (size_t i = 0; i < la; i++) diff |= (unsigned char)a[i];
            (void)diff;
            puts("false");
            continue;
        }
        unsigned char diff = 0;
        for (size_t i = 0; i < la; i++) {
            diff |= (unsigned char)(a[i] ^ b[i]);
        }
        puts(diff == 0 ? "true" : "false");
    }
    return 0;
}
