/* Reference for 163_bigint_divmod_c: Knuth Algorithm D, base 2^32. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *buf;
static size_t pos, len;

static uint64_t next_u(void) {
    while (pos < len && (buf[pos] < '0' || buf[pos] > '9')) pos++;
    uint64_t x = 0;
    while (pos < len && buf[pos] >= '0' && buf[pos] <= '9') x = x * 10 + (uint64_t)(buf[pos++] - '0');
    return x;
}

static char *out;
static size_t olen, ocap;

static void put_u(uint64_t x) {
    char tmp[24];
    int k = 0;
    do { tmp[k++] = (char)('0' + x % 10); x /= 10; } while (x);
    if (olen + 32 > ocap) { ocap = ocap * 2 + 64; out = realloc(out, ocap); }
    while (k) out[olen++] = tmp[--k];
}
static void put_c(char c) {
    if (olen + 2 > ocap) { ocap = ocap * 2 + 64; out = realloc(out, ocap); }
    out[olen++] = c;
}
static void put_limbs(const uint32_t *a, size_t n) {
    while (n > 1 && a[n - 1] == 0) n--;
    for (size_t i = 0; i < n; i++) { if (i) put_c(' '); put_u(a[i]); }
    put_c('\n');
}

static int nlz(uint32_t x) {
    int s = 0;
    while (!(x & 0x80000000u)) { x <<= 1; s++; }
    return s;
}

int main(void) {
    size_t cap = 1 << 20;
    buf = malloc(cap);
    size_t r;
    while ((r = fread(buf + len, 1, cap - len, stdin)) > 0) {
        len += r;
        if (len == cap) { cap *= 2; buf = realloc(buf, cap); }
    }
    uint64_t T = next_u();
    for (uint64_t tc = 0; tc < T; tc++) {
        size_t lu = (size_t)next_u(), lv = (size_t)next_u();
        uint32_t *u = calloc(lu + 2, 4), *v = calloc(lv + 1, 4);
        for (size_t i = 0; i < lu; i++) u[i] = (uint32_t)next_u();
        for (size_t i = 0; i < lv; i++) v[i] = (uint32_t)next_u();
        while (lu > 0 && u[lu - 1] == 0) lu--;
        while (lv > 1 && v[lv - 1] == 0) lv--;
        size_t n = lv;
        uint64_t d3 = 0, d6 = 0;
        if (lu < n) {
            uint32_t z = 0;
            put_limbs(&z, 1);
            if (lu == 0) put_limbs(&z, 1); else put_limbs(u, lu);
        } else if (n == 1) {
            uint32_t *q = calloc(lu, 4);
            uint64_t rem = 0;
            for (size_t i = lu; i-- > 0;) {
                uint64_t cur = (rem << 32) | u[i];
                q[i] = (uint32_t)(cur / v[0]);
                rem = cur % v[0];
            }
            uint32_t r32 = (uint32_t)rem;
            put_limbs(q, lu);
            put_limbs(&r32, 1);
            free(q);
        } else {
            size_t m = lu - n;
            int s = nlz(v[n - 1]);
            uint32_t *vn = malloc(n * 4), *un = malloc((lu + 1) * 4), *q = calloc(m + 1, 4);
            if (s) {
                for (size_t i = n - 1; i > 0; i--) vn[i] = (v[i] << s) | (v[i - 1] >> (32 - s));
                vn[0] = v[0] << s;
                un[lu] = u[lu - 1] >> (32 - s);
                for (size_t i = lu - 1; i > 0; i--) un[i] = (u[i] << s) | (u[i - 1] >> (32 - s));
                un[0] = u[0] << s;
            } else {
                memcpy(vn, v, n * 4);
                memcpy(un, u, lu * 4);
                un[lu] = 0;
            }
            const uint64_t B = 1ull << 32;
            for (size_t j = m + 1; j-- > 0;) {
                uint64_t num = ((uint64_t)un[j + n] << 32) | un[j + n - 1];
                uint64_t qhat = num / vn[n - 1], rhat = num % vn[n - 1];
                while (qhat >= B || qhat * vn[n - 2] > ((rhat << 32) | un[j + n - 2])) {
                    qhat--;
                    d3++;
                    rhat += vn[n - 1];
                    if (rhat >= B) break;
                }
                int64_t t;
                uint64_t k = 0;
                for (size_t i = 0; i < n; i++) {
                    uint64_t p = qhat * vn[i];
                    t = (int64_t)un[i + j] - (int64_t)k - (int64_t)(p & 0xFFFFFFFFu);
                    un[i + j] = (uint32_t)t;
                    k = (p >> 32) - (uint64_t)(t >> 32);
                }
                t = (int64_t)un[j + n] - (int64_t)k;
                un[j + n] = (uint32_t)t;
                if (t < 0) {
                    d6++;
                    qhat--;
                    uint64_t c = 0;
                    for (size_t i = 0; i < n; i++) {
                        uint64_t sum = (uint64_t)un[i + j] + vn[i] + c;
                        un[i + j] = (uint32_t)sum;
                        c = sum >> 32;
                    }
                    un[j + n] += (uint32_t)c;
                }
                q[j] = (uint32_t)qhat;
            }
            uint32_t *rr = malloc(n * 4);
            if (s) {
                for (size_t i = 0; i < n - 1; i++) rr[i] = (un[i] >> s) | (un[i + 1] << (32 - s));
                rr[n - 1] = un[n - 1] >> s;
            } else {
                memcpy(rr, un, n * 4);
            }
            put_limbs(q, m + 1);
            put_limbs(rr, n);
            free(vn); free(un); free(q); free(rr);
        }
        put_u(d3); put_c(' '); put_u(d6); put_c('\n');
        free(u); free(v);
    }
    fwrite(out, 1, olen, stdout);
    return 0;
}
