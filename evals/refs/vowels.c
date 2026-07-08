#include <stdio.h>
#include <ctype.h>

int main(void) {
    int c;
    int count = 0;
    int have_line = 0;
    while ((c = getchar()) != EOF) {
        if (c == '\n') {
            printf("%d\n", count);
            count = 0;
            have_line = 0;
        } else {
            have_line = 1;
            int lc = tolower(c);
            if (lc == 'a' || lc == 'e' || lc == 'i' || lc == 'o' || lc == 'u')
                count++;
        }
    }
    if (have_line)
        printf("%d\n", count);
    return 0;
}
