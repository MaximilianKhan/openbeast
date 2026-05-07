#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static int parse_byte(const char *s) {
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        return (int)strtol(s+2, NULL, 16);
    }
    return atoi(s);
}

static int gfmul(int a, int b) {
    int p = 0;
    for (int i = 0; i < 8; i++) {
        if (b & 1) p ^= a;
        b >>= 1;
        int hi = a & 0x80;
        a = (a << 1) & 0xFF;
        if (hi) a ^= 0x1B;
    }
    return p;
}

int main(void) {
    int T;
    if (scanf("%d", &T) != 1) return 1;
    char op[8], sa[16], sb[16];
    while (T--) {
        if (scanf("%7s %15s %15s", op, sa, sb) != 3) return 1;
        int a = parse_byte(sa);
        int b = parse_byte(sb);
        if (op[0] == '+') printf("%d\n", a ^ b);
        else printf("%d\n", gfmul(a, b));
    }
    return 0;
}
