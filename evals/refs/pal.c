#include <stdio.h>
#include <ctype.h>
#include <string.h>

int main(void) {
    char line[4100];
    while (fgets(line, sizeof(line), stdin) != NULL) {
        char clean[4100];
        int n = 0;
        for (int i = 0; line[i] != '\0'; i++) {
            unsigned char c = (unsigned char)line[i];
            if (c == '\n') continue;
            if (isalnum(c))
                clean[n++] = (char)tolower(c);
        }
        int pal = 1;
        for (int i = 0, j = n - 1; i < j; i++, j--) {
            if (clean[i] != clean[j]) { pal = 0; break; }
        }
        printf("%s\n", pal ? "true" : "false");
    }
    return 0;
}
