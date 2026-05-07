const std = @import("std");

fn mulMod(a: u128, b: u128, m: u128) u128 {
    return (a * b) % m;
}

fn powMod(base: u128, exp: u128, m: u128) u128 {
    var result: u128 = 1;
    var b = base % m;
    var e = exp;
    while (e > 0) {
        if ((e & 1) == 1) result = mulMod(result, b, m);
        b = mulMod(b, b, m);
        e >>= 1;
    }
    return result;
}

fn millerRabin(n: u64) bool {
    if (n < 2) return false;
    const small_primes = [_]u64{ 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37 };
    for (small_primes) |p| {
        if (n == p) return true;
        if (n % p == 0) return false;
    }
    var d: u64 = n - 1;
    var s: u64 = 0;
    while ((d & 1) == 0) {
        d >>= 1;
        s += 1;
    }
    const N: u128 = n;
    for (small_primes) |a| {
        var x = powMod(a, d, N);
        if (x == 1 or x == N - 1) continue;
        var composite = true;
        var i: u64 = 0;
        while (i + 1 < s) : (i += 1) {
            x = mulMod(x, x, N);
            if (x == N - 1) {
                composite = false;
                break;
            }
        }
        if (composite) return false;
    }
    return true;
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var case: usize = 0;
    while (case < t) : (case += 1) {
        const n = try std.fmt.parseInt(u64, it.next().?, 10);
        try w.writeAll(if (millerRabin(n)) "true\n" else "false\n");
    }
    try w.flush();
}
