// WRONG (163_f): forgets to shift the remainder back by s.
const std = @import("std");

const Res = struct { q: []u32, r: []u32, d3: u64, d6: u64 };

fn trim(a: []u32) []u32 {
    var n = a.len;
    while (n > 1 and a[n - 1] == 0) n -= 1;
    return a[0..n];
}

// (hi:lo) << s, top 32 bits; s in 0..31, no shift-by-32 edge.
fn shl(hi: u32, lo: u32, s: u6) u32 {
    const w: u64 = (@as(u64, hi) << 32) | lo;
    return @truncate((w << s) >> 32);
}

fn divmodD(al: std.mem.Allocator, uin: []u32, vin: []u32) !Res {
    var lu = uin.len;
    while (lu > 0 and uin[lu - 1] == 0) lu -= 1;
    const u = uin[0..lu];
    const v = trim(vin);
    const n = v.len;
    const zero = try al.alloc(u32, 1);
    zero[0] = 0;
    if (lu < n) {
        return .{ .q = zero, .r = if (lu == 0) zero else u, .d3 = 0, .d6 = 0 };
    }
    if (n == 1) {
        const q = try al.alloc(u32, lu);
        const d: u64 = v[0];
        var rem: u64 = 0;
        var i = lu;
        while (i > 0) {
            i -= 1;
            const cur = (rem << 32) | u[i];
            q[i] = @intCast(cur / d);
            rem = cur % d;
        }
        const r = try al.alloc(u32, 1);
        r[0] = @intCast(rem);
        return .{ .q = trim(q), .r = r, .d3 = 0, .d6 = 0 };
    }
    const m = lu - n;
    const s: u6 = @clz(v[n - 1]);
    const vn = try al.alloc(u32, n);
    const un = try al.alloc(u32, lu + 1);
    var i: usize = n - 1;
    while (i > 0) : (i -= 1) vn[i] = shl(v[i], v[i - 1], s);
    vn[0] = shl(v[0], 0, s);
    un[lu] = shl(0, u[lu - 1], s);
    i = lu - 1;
    while (i > 0) : (i -= 1) un[i] = shl(u[i], u[i - 1], s);
    un[0] = shl(u[0], 0, s);

    const q = try al.alloc(u32, m + 1);
    const b: u64 = 1 << 32;
    const v1: u64 = vn[n - 1];
    const v2: u64 = vn[n - 2];
    var d3: u64 = 0;
    var d6: u64 = 0;
    var jj: usize = m + 1;
    while (jj > 0) {
        jj -= 1;
        const j = jj;
        const num = (@as(u64, un[j + n]) << 32) | un[j + n - 1];
        var qhat = num / v1;
        var rhat = num % v1;
        while (qhat >= b or qhat * v2 > ((rhat << 32) | un[j + n - 2])) {
            qhat -= 1;
            d3 += 1;
            rhat += v1;
            if (rhat >= b) break;
        }
        var carry: u64 = 0;
        var borrow: u64 = 0;
        var k: usize = 0;
        while (k < n) : (k += 1) {
            const p = qhat * vn[k] + carry;
            carry = p >> 32;
            const t = @as(u64, un[k + j]) -% (p & 0xFFFF_FFFF) -% borrow;
            un[k + j] = @truncate(t);
            borrow = (t >> 32) & 1;
        }
        const t = @as(u64, un[j + n]) -% carry -% borrow;
        un[j + n] = @truncate(t);
        if (t >> 63 != 0) {
            d6 += 1;
            qhat -= 1;
            var c: u64 = 0;
            k = 0;
            while (k < n) : (k += 1) {
                const sum = @as(u64, un[k + j]) + vn[k] + c;
                un[k + j] = @truncate(sum);
                c = sum >> 32;
            }
            un[j + n] +%= @as(u32, @truncate(c));
        }
        q[j] = @intCast(qhat);
    }
    const r = try al.alloc(u32, n);
    for (r, 0..) |*x, k| {
        const hi: u64 = if (k + 1 < n) un[k + 1] else 0;
        _ = hi;
        x.* = un[k];
    }
    return .{ .q = trim(q), .r = trim(r), .d3 = d3, .d6 = d6 };
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [65536]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const rd = &fr.interface;
    var data: std.ArrayList(u8) = .empty;
    try rd.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");

    var out_buf: [65536]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var tc: usize = 0;
    while (tc < t) : (tc += 1) {
        const lu = try std.fmt.parseInt(usize, it.next().?, 10);
        const lv = try std.fmt.parseInt(usize, it.next().?, 10);
        const u = try arena.alloc(u32, lu);
        const v = try arena.alloc(u32, lv);
        for (u) |*x| x.* = try std.fmt.parseInt(u32, it.next().?, 10);
        for (v) |*x| x.* = try std.fmt.parseInt(u32, it.next().?, 10);
        const res = try divmodD(arena, u, v);
        for (res.q, 0..) |x, idx| {
            if (idx > 0) try w.writeByte(' ');
            try w.print("{d}", .{x});
        }
        try w.writeByte('\n');
        for (res.r, 0..) |x, idx| {
            if (idx > 0) try w.writeByte(' ');
            try w.print("{d}", .{x});
        }
        try w.writeByte('\n');
        try w.print("{d} {d}\n", .{ res.d3, res.d6 });
    }
    try w.flush();
}
