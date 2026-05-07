#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static char code[65536], inp[65536];
static int pairs[65536];
static unsigned char tape[30000];

static void run(void) {
    int n = (int)strlen(code);
    int stack[65536]; int top = 0;
    for (int i = 0; i < n; i++) {
        if (code[i] == '[') stack[top++] = i;
        else if (code[i] == ']') {
            int j = stack[--top];
            pairs[i] = j; pairs[j] = i;
        }
    }
    memset(tape, 0, sizeof(tape));
    int ptr = 0, ip = 0, ipos = 0;
    int ilen = (int)strlen(inp);
    while (ip < n) {
        switch (code[ip]) {
            case '>': ptr++; break;
            case '<': ptr--; break;
            case '+': tape[ptr]++; break;
            case '-': tape[ptr]--; break;
            case '.': putchar(tape[ptr]); break;
            case ',':
                tape[ptr] = (ipos < ilen) ? (unsigned char)inp[ipos] : 0;
                ipos++; break;
            case '[':
                if (tape[ptr] == 0) ip = pairs[ip];
                break;
            case ']':
                if (tape[ptr] != 0) ip = pairs[ip];
                break;
        }
        ip++;
    }
    putchar('\n');
}

static void readline(char *buf) {
    if (!fgets(buf, 65536, stdin)) buf[0] = '\0';
    int len = (int)strlen(buf);
    if (len > 0 && buf[len-1] == '\n') buf[--len] = '\0';
}

int main(void) {
    char tline[64];
    if (!fgets(tline, sizeof(tline), stdin)) return 1;
    int T = atoi(tline);
    while (T--) {
        readline(code);
        readline(inp);
        run();
    }
    return 0;
}
