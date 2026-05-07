const std = @import("std");

const MOD: u64 = 998244353;
const G: u64 = 3;

fn mulMod(a: u64, b: u64) u64 {
    return @intCast((@as(u128, a) * @as(u128, b)) % @as(u128, MOD));
}

fn powMod(base: u64, exp: u64) u64 {
    var result: u64 = 1;
    var b = base % MOD;
    var e = exp;
    while (e > 0) {
        if ((e & 1) == 1) result = mulMod(result, b);
        b = mulMod(b, b);
        e >>= 1;
    }
    return result;
}

fn ntt(a: []u64, invert: bool) void {
    const n = a.len;
    // Bit-reverse permutation
    var j: usize = 0;
    var i: usize = 1;
    while (i < n) : (i += 1) {
        var bit = n >> 1;
        while ((j & bit) != 0) {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if (i < j) {
            const tmp = a[i];
            a[i] = a[j];
            a[j] = tmp;
        }
    }
    // Butterfly
    var len: usize = 2;
    while (len <= n) : (len <<= 1) {
        const exp_val = (MOD - 1) / @as(u64, @intCast(len));
        var w_n = powMod(G, exp_val);
        if (invert) w_n = powMod(w_n, MOD - 2);
        var blk: usize = 0;
        while (blk < n) : (blk += len) {
            var w: u64 = 1;
            var k: usize = 0;
            while (k < len / 2) : (k += 1) {
                const u = a[blk + k];
                const v = mulMod(a[blk + k + len / 2], w);
                a[blk + k] = if (u + v >= MOD) u + v - MOD else u + v;
                a[blk + k + len / 2] = if (u >= v) u - v else u + MOD - v;
                w = mulMod(w, w_n);
            }
        }
    }
    if (invert) {
        const n_inv = powMod(@as(u64, @intCast(n)), MOD - 2);
        for (a) |*x| x.* = mulMod(x.*, n_inv);
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

        if (na == 0 or nb == 0) {
            // Skip values (none) and emit "0"
            try w.writeAll("0\n");
            continue;
        }

        const a = try arena.alloc(u64, na);
        const b = try arena.alloc(u64, nb);
        var i: usize = 0;
        while (i < na) : (i += 1) a[i] = try std.fmt.parseInt(u64, it.next().?, 10);
        i = 0;
        while (i < nb) : (i += 1) b[i] = try std.fmt.parseInt(u64, it.next().?, 10);

        const out_len = na + nb - 1;
        var sz: usize = 1;
        while (sz < out_len) sz <<= 1;

        const fa = try arena.alloc(u64, sz);
        const fb = try arena.alloc(u64, sz);
        @memset(fa, 0);
        @memset(fb, 0);
        for (a, 0..) |x, idx| fa[idx] = x;
        for (b, 0..) |x, idx| fb[idx] = x;

        ntt(fa, false);
        ntt(fb, false);
        for (fa, 0..) |x, idx| fa[idx] = mulMod(x, fb[idx]);
        ntt(fa, true);

        var j: usize = 0;
        while (j < out_len) : (j += 1) {
            if (j > 0) try w.writeByte(' ');
            try w.print("{d}", .{fa[j]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
