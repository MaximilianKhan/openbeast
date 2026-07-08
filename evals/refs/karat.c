/* Karatsuba multiplication on little-endian base-256 byte arrays.
 * Pure uint8_t digit arithmetic with carries; no big-integer library.
 * gcc -O2 -std=c11 -Wall -Wextra
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define CUTOFF 32

/* A number is a heap-allocated little-endian byte buffer plus a length.
 * Every returned buffer is trimmed (no leading zero digits, min length 1). */
typedef struct {
    uint8_t *d;
    size_t len;
} Num;

static Num make_num(size_t len) {
    Num n;
    n.d = (uint8_t *)calloc(len ? len : 1, 1);
    if (!n.d) { fprintf(stderr, "oom\n"); exit(1); }
    n.len = len;
    return n;
}

static void trim(Num *n) {
    while (n->len > 1 && n->d[n->len - 1] == 0) n->len--;
}

static Num add(const uint8_t *a, size_t la, const uint8_t *b, size_t lb) {
    size_t n = la > lb ? la : lb;
    Num out = make_num(n + 1);
    int carry = 0;
    for (size_t i = 0; i < n; i++) {
        int s = carry;
        if (i < la) s += a[i];
        if (i < lb) s += b[i];
        out.d[i] = (uint8_t)(s & 0xFF);
        carry = s >> 8;
    }
    out.d[n] = (uint8_t)carry;
    trim(&out);
    return out;
}

/* a - b, assuming a >= b. */
static Num sub(const uint8_t *a, size_t la, const uint8_t *b, size_t lb) {
    Num out = make_num(la);
    int borrow = 0;
    for (size_t i = 0; i < la; i++) {
        int d = (int)a[i] - borrow;
        if (i < lb) d -= b[i];
        if (d < 0) { d += 256; borrow = 1; } else { borrow = 0; }
        out.d[i] = (uint8_t)d;
    }
    trim(&out);
    return out;
}

static Num school(const uint8_t *a, size_t la, const uint8_t *b, size_t lb) {
    size_t n = la + lb;
    uint32_t *acc = (uint32_t *)calloc(n ? n : 1, sizeof(uint32_t));
    if (!acc) { fprintf(stderr, "oom\n"); exit(1); }
    for (size_t i = 0; i < la; i++) {
        uint32_t x = a[i];
        if (x == 0) continue;
        uint32_t carry = 0;
        for (size_t j = 0; j < lb; j++) {
            uint32_t s = acc[i + j] + x * (uint32_t)b[j] + carry;
            acc[i + j] = s & 0xFF;
            carry = s >> 8;
        }
        size_t k = i + lb;
        while (carry) {
            uint32_t s = acc[k] + carry;
            acc[k] = s & 0xFF;
            carry = s >> 8;
            k++;
        }
    }
    Num out = make_num(n);
    for (size_t i = 0; i < n; i++) out.d[i] = (uint8_t)acc[i];
    free(acc);
    trim(&out);
    return out;
}

static Num karat(const uint8_t *a, size_t la, const uint8_t *b, size_t lb) {
    if (la <= CUTOFF || lb <= CUTOFF)
        return school(a, la, b, lb);

    size_t m = (la < lb ? la : lb) / 2;
    const uint8_t *a0 = a, *a1 = a + m;
    const uint8_t *b0 = b, *b1 = b + m;
    size_t la0 = m, la1 = la - m;
    size_t lb0 = m, lb1 = lb - m;

    Num z0 = karat(a0, la0, b0, lb0);
    Num z2 = karat(a1, la1, b1, lb1);
    Num sa = add(a0, la0, a1, la1);
    Num sb = add(b0, lb0, b1, lb1);
    Num zm = karat(sa.d, sa.len, sb.d, sb.len);
    free(sa.d); free(sb.d);
    Num t1 = sub(zm.d, zm.len, z0.d, z0.len);
    free(zm.d);
    Num z1 = sub(t1.d, t1.len, z2.d, z2.len);
    free(t1.d);

    size_t total = la + lb + 4;
    uint32_t *acc = (uint32_t *)calloc(total, sizeof(uint32_t));
    if (!acc) { fprintf(stderr, "oom\n"); exit(1); }
    for (size_t i = 0; i < z0.len; i++) acc[i] += z0.d[i];
    for (size_t i = 0; i < z1.len; i++) acc[i + m] += z1.d[i];
    for (size_t i = 0; i < z2.len; i++) acc[i + 2 * m] += z2.d[i];
    free(z0.d); free(z1.d); free(z2.d);

    uint32_t carry = 0;
    for (size_t i = 0; i < total; i++) {
        uint32_t s = acc[i] + carry;
        acc[i] = s & 0xFF;
        carry = s >> 8;
    }
    Num out = make_num(total);
    for (size_t i = 0; i < total; i++) out.d[i] = (uint8_t)acc[i];
    free(acc);
    trim(&out);
    return out;
}

/* Fast stdin integer scanner. */
static char *g_buf;
static size_t g_pos, g_size;

static int read_int(void) {
    int n = 0;
    while (g_pos < g_size) {
        char c = g_buf[g_pos];
        if (c >= '0' && c <= '9') break;
        g_pos++;
    }
    while (g_pos < g_size) {
        char c = g_buf[g_pos];
        if (c < '0' || c > '9') break;
        n = n * 10 + (c - '0');
        g_pos++;
    }
    return n;
}

int main(void) {
    /* Slurp all of stdin. */
    size_t cap = 1 << 20;
    g_buf = (char *)malloc(cap);
    if (!g_buf) { fprintf(stderr, "oom\n"); exit(1); }
    g_size = 0;
    size_t r;
    while ((r = fread(g_buf + g_size, 1, cap - g_size, stdin)) > 0) {
        g_size += r;
        if (g_size == cap) {
            cap *= 2;
            char *nb = (char *)realloc(g_buf, cap);
            if (!nb) { fprintf(stderr, "oom\n"); exit(1); }
            g_buf = nb;
        }
    }

    char *outbuf = (char *)malloc(1 << 20);
    size_t ocap = 1 << 20, olen = 0;
    if (!outbuf) { fprintf(stderr, "oom\n"); exit(1); }

    int t = read_int();
    for (int c = 0; c < t; c++) {
        int na = read_int();
        int nb = read_int();
        uint8_t *a = (uint8_t *)malloc((size_t)na ? (size_t)na : 1);
        uint8_t *b = (uint8_t *)malloc((size_t)nb ? (size_t)nb : 1);
        if (!a || !b) { fprintf(stderr, "oom\n"); exit(1); }
        for (int i = 0; i < na; i++) a[i] = (uint8_t)read_int();
        for (int i = 0; i < nb; i++) b[i] = (uint8_t)read_int();
        size_t la = na, lb = nb;
        while (la > 1 && a[la - 1] == 0) la--;
        while (lb > 1 && b[lb - 1] == 0) lb--;

        Num p = karat(a, la, b, lb);
        free(a); free(b);

        /* Emit p as space-separated decimals. Worst case ~4 chars/digit. */
        if (olen + p.len * 4 + 2 > ocap) {
            while (olen + p.len * 4 + 2 > ocap) ocap *= 2;
            outbuf = (char *)realloc(outbuf, ocap);
            if (!outbuf) { fprintf(stderr, "oom\n"); exit(1); }
        }
        for (size_t i = 0; i < p.len; i++) {
            if (i > 0) outbuf[olen++] = ' ';
            unsigned v = p.d[i];
            char tmp[4];
            int tl = 0;
            if (v == 0) tmp[tl++] = '0';
            while (v) { tmp[tl++] = (char)('0' + v % 10); v /= 10; }
            while (tl) outbuf[olen++] = tmp[--tl];
        }
        outbuf[olen++] = '\n';
        free(p.d);
    }
    fwrite(outbuf, 1, olen, stdout);
    free(outbuf);
    free(g_buf);
    return 0;
}
