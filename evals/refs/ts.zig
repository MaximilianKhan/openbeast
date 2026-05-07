const std = @import("std");

fn powMod(base: u128, exp: u128, m: u128) u128 {
    if (m == 1) return 0;
    var result: u128 = 1;
    var b = base % m;
    var e = exp;
    while (e > 0) {
        if ((e & 1) == 1) result = (result * b) % m;
        b = (b * b) % m;
        e >>= 1;
    }
    return result;
}

fn tonelli(n: u128, p: u128) ?u128 {
    if (n == 0) return 0;
    if (p == 2) return n % 2;
    if (powMod(n, (p - 1) / 2, p) != 1) return null;
    if (p % 4 == 3) return powMod(n, (p + 1) / 4, p);

    var q = p - 1;
    var s: u128 = 0;
    while (q % 2 == 0) {
        q /= 2;
        s += 1;
    }

    var z: u128 = 2;
    while (powMod(z, (p - 1) / 2, p) != p - 1) z += 1;

    var m = s;
    var c = powMod(z, q, p);
    var t = powMod(n, q, p);
    var r = powMod(n, (q + 1) / 2, p);

    while (true) {
        if (t == 1) return r;
        var i: u128 = 0;
        var tmp = t;
        while (tmp != 1) {
            tmp = (tmp * tmp) % p;
            i += 1;
            if (i >= m) return null;
        }
        var b = c;
        var k: u128 = 0;
        while (k < m - i - 1) : (k += 1) b = (b * b) % p;
        m = i;
        c = (b * b) % p;
        t = (t * c) % p;
        r = (r * b) % p;
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

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var case: usize = 0;
    while (case < t) : (case += 1) {
        const n = try std.fmt.parseInt(u128, it.next().?, 10);
        const p = try std.fmt.parseInt(u128, it.next().?, 10);
        if (tonelli(n, p)) |root| {
            try w.print("{d}\n", .{root});
        } else {
            try w.writeAll("NONE\n");
        }
    }
    try w.flush();
}
