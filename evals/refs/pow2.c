#include <stdio.h>

int main(void) {
    long long n;
    while (scanf("%lld", &n) == 1) {
        if (n > 0 && (n & (n - 1)) == 0)
            printf("true\n");
        else
            printf("false\n");
    }
    return 0;
}
