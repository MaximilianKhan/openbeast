const std = @import("std");

fn egcd(a: i64, b: i64) struct { g: i64, x: i64, y: i64 } {
    if (b == 0) return .{ .g = a, .x = 1, .y = 0 };
    const r = egcd(b, @mod(a, b));
    return .{ .g = r.g, .x = r.y, .y = r.x - @divTrunc(a, b) * r.y };
}
fn modinv(a: i64, n: i64) ?i64 {
    const aa = @mod(@mod(a, n) + n, n);
    const r = egcd(aa, n);
    if (r.g != 1) return null;
    return @mod(@mod(r.x, n) + n, n);
}
fn igcd(a_in: i64, b_in: i64) i64 {
    var a = if (a_in < 0) -a_in else a_in;
    var b = if (b_in < 0) -b_in else b_in;
    while (b != 0) {
        const t = @mod(a, b);
        a = b;
        b = t;
    }
    return a;
}
fn crt(rs: []const i64, ms: []const i64) i64 {
    var m_acc: i64 = 1;
    var x: i64 = 0;
    for (rs, ms) |r, m| {
        const g = igcd(m_acc, m);
        if (@mod(@mod(r - x, g) + g, g) != 0) return -1;
        const m2 = @divTrunc(m, g);
        const M2 = @divTrunc(m_acc, g);
        const inv = modinv(@mod(M2, m2), m2) orelse return -1;
        const k = @mod(@mod(@divTrunc(r - x, g) * inv, m2) + m2, m2);
        x = x + m_acc * k;
        m_acc = m_acc * m2;
        x = @mod(@mod(x, m_acc) + m_acc, m_acc);
    }
    if (m_acc == 0) return 0;
    return @mod(@mod(x, m_acc) + m_acc, m_acc);
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
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const k = try std.fmt.parseInt(usize, it.next().?, 10);
        const rs = try arena.alloc(i64, k);
        const ms = try arena.alloc(i64, k);
        var i: usize = 0;
        while (i < k) : (i += 1) {
            rs[i] = try std.fmt.parseInt(i64, it.next().?, 10);
            ms[i] = try std.fmt.parseInt(i64, it.next().?, 10);
        }
        try w.print("{d}\n", .{crt(rs, ms)});
    }
    try w.flush();
}
