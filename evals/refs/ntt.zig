// Polynomial convolution via NTT mod p = 998244353, primitive root g = 3.
// Iterative radix-2 with bit-reversal permutation. u128 modular multiply.
// Matches the reference Python NTT byte-for-byte. Zig 0.16 IO API.
const std = @import("std");

const MOD: u64 = 998244353;

inline fn mulmod(a: u64, b: u64) u64 {
    return @intCast((@as(u128, a) * @as(u128, b)) % MOD);
}

fn powmod(a_in: u64, e_in: u64) u64 {
    var r: u64 = 1;
    var a: u64 = a_in % MOD;
    var e: u64 = e_in;
    while (e > 0) {
        if (e & 1 == 1) r = mulmod(r, a);
        a = mulmod(a, a);
        e >>= 1;
    }
    return r;
}

fn ntt(a: []u64, inv: bool) void {
    const n = a.len;
    var j: usize = 0;
    var i: usize = 1;
    while (i < n) : (i += 1) {
        var bit = n >> 1;
        while (j & bit != 0) : (bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            const t = a[i];
            a[i] = a[j];
            a[j] = t;
        }
    }
    var len: usize = 2;
    while (len <= n) : (len <<= 1) {
        var w = powmod(3, (MOD - 1) / @as(u64, len));
        if (inv) w = powmod(w, MOD - 2);
        const half = len >> 1;
        var base: usize = 0;
        while (base < n) : (base += len) {
            var wn: u64 = 1;
            var k: usize = 0;
            while (k < half) : (k += 1) {
                const u = a[base + k];
                const v = mulmod(a[base + k + half], wn);
                a[base + k] = (u + v) % MOD;
                a[base + k + half] = (u + MOD - v) % MOD;
                wn = mulmod(wn, w);
            }
        }
    }
    if (inv) {
        const ninv = powmod(@as(u64, n), MOD - 2);
        for (a) |*x| x.* = mulmod(x.*, ninv);
    }
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");

    var out_buf: [16384]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var case: usize = 0;
    while (case < t) : (case += 1) {
        const na = try std.fmt.parseInt(usize, it.next().?, 10);
        const nb = try std.fmt.parseInt(usize, it.next().?, 10);
        const a = try arena.alloc(u64, na);
        const b = try arena.alloc(u64, nb);
        var idx: usize = 0;
        while (idx < na) : (idx += 1) {
            a[idx] = (try std.fmt.parseInt(u64, it.next().?, 10)) % MOD;
        }
        idx = 0;
        while (idx < nb) : (idx += 1) {
            b[idx] = (try std.fmt.parseInt(u64, it.next().?, 10)) % MOD;
        }
        if (na == 0 or nb == 0) {
            try w.writeAll("0\n");
            continue;
        }
        const rl = na + nb - 1;
        var n: usize = 1;
        while (n < rl) n <<= 1;
        const fa = try arena.alloc(u64, n);
        const fb = try arena.alloc(u64, n);
        @memset(fa, 0);
        @memset(fb, 0);
        @memcpy(fa[0..na], a);
        @memcpy(fb[0..nb], b);
        ntt(fa, false);
        ntt(fb, false);
        var m: usize = 0;
        while (m < n) : (m += 1) fa[m] = mulmod(fa[m], fb[m]);
        ntt(fa, true);
        m = 0;
        while (m < rl) : (m += 1) {
            if (m != 0) try w.writeByte(' ');
            try w.print("{d}", .{fa[m]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
