const std = @import("std");

fn gcdU(a_in: u64, b_in: u64) u64 {
    var a = a_in; var b = b_in;
    while (b != 0) {
        const t = a % b; a = b; b = t;
    }
    return a;
}

fn mulmod(a: u64, b: u64, m: u64) u64 {
    return @intCast((@as(u128, a) * @as(u128, b)) % @as(u128, m));
}

fn pollard(n: u64) u64 {
    if (n % 2 == 0) return 2;
    var c: u64 = 1;
    while (true) {
        var x: u64 = 2; var y: u64 = 2; var d: u64 = 1;
        while (d == 1) {
            x = (mulmod(x, x, n) + c) % n;
            y = (mulmod(y, y, n) + c) % n;
            y = (mulmod(y, y, n) + c) % n;
            const diff = if (x > y) x - y else y - x;
            d = gcdU(diff, n);
        }
        if (d != n) return d;
        c += 1;
    }
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
    var k: usize = 0;
    while (k < t) : (k += 1) {
        const n = try std.fmt.parseInt(u64, it.next().?, 10);
        try w.print("{d}\n", .{pollard(n)});
    }
    try w.flush();
}
