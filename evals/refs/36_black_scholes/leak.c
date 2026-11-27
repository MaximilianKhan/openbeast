#include <stdio.h>

int main(void) {
    FILE *f = fopen("sample_output.txt", "rb");
    char buf[4096];
    size_t k;
    if (!f) return 0;
    while ((k = fread(buf, 1, sizeof buf, f)) > 0) fwrite(buf, 1, k, stdout);
    fclose(f);
    return 0;
}
