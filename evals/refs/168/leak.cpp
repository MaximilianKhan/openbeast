/* LEAK PROBE: prints every file it can find in the fixture dir except its own. */
#include <dirent.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    DIR *d = opendir("/tmp/eval_linz");
    struct dirent *e;
    char p[4096], b[4096];
    if (!d) return 0;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.' || strncmp(e->d_name, "linz", 4) == 0) continue;
        snprintf(p, sizeof p, "/tmp/eval_linz/%s", e->d_name);
        FILE *f = fopen(p, "r");
        size_t n;
        if (!f) continue;
        while ((n = fread(b, 1, sizeof b, f)) > 0) fwrite(b, 1, n, stdout);
        fclose(f);
    }
    closedir(d);
    return 0;
}
