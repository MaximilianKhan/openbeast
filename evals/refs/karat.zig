const std = @import("std");

// Karatsuba multiplication on little-endian base-256 byte arrays.
// []u8 digit arrays, no arbitrary-width Int collapse. Zig 0.16 idioms.

const CUTOFF: usize = 32;

fn trim(a: []u8) []u8 {
    var n = a.len;
    while (n > 1 and a[n - 1] == 0) n -= 1;
    return a[0..n];
}

fn add(al: std.mem.Allocator, a: []const u8, b: []const u8) ![]u8 {
    const n = @max(a.len, b.len);
    const out = try al.alloc(u8, n + 1);
    var carry: u32 = 0;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        var s: u32 = carry;
        if (i < a.len) s += a[i];
        if (i < b.len) s += b[i];
        out[i] = @intCast(s & 0xFF);
        carry = s >> 8;
    }
    out[n] = @intCast(carry);
    return trim(out);
}

// a - b, assuming a >= b.
fn sub(al: std.mem.Allocator, a: []const u8, b: []const u8) ![]u8 {
    const out = try al.alloc(u8, a.len);
    var borrow: i32 = 0;
    var i: usize = 0;
    while (i < a.len) : (i += 1) {
        var d: i32 = @as(i32, a[i]) - borrow;
        if (i < b.len) d -= b[i];
        if (d < 0) {
            d += 256;
            borrow = 1;
        } else {
            borrow = 0;
        }
        out[i] = @intCast(d);
    }
    return trim(out);
}

fn school(al: std.mem.Allocator, a: []const u8, b: []const u8) ![]u8 {
    const n = a.len + b.len;
    const acc = try al.alloc(u32, n);
    @memset(acc, 0);
    var i: usize = 0;
    while (i < a.len) : (i += 1) {
        const x: u32 = a[i];
        if (x == 0) continue;
        var carry: u32 = 0;
        var j: usize = 0;
        while (j < b.len) : (j += 1) {
            const s = acc[i + j] + x * @as(u32, b[j]) + carry;
            acc[i + j] = s & 0xFF;
            carry = s >> 8;
        }
        var k = i + b.len;
        while (carry != 0) {
            const s = acc[k] + carry;
            acc[k] = s & 0xFF;
            carry = s >> 8;
            k += 1;
        }
    }
    const out = try al.alloc(u8, n);
    i = 0;
    while (i < n) : (i += 1) out[i] = @intCast(acc[i]);
    return trim(out);
}

fn karat(al: std.mem.Allocator, a: []const u8, b: []const u8) ![]u8 {
    if (a.len <= CUTOFF or b.len <= CUTOFF) return school(al, a, b);

    const m = @min(a.len, b.len) / 2;
    const a0 = a[0..m];
    const a1 = a[m..];
    const b0 = b[0..m];
    const b1 = b[m..];

    const z0 = try karat(al, a0, b0);
    const z2 = try karat(al, a1, b1);
    const sa = try add(al, a0, a1);
    const sb = try add(al, b0, b1);
    const zm = try karat(al, sa, sb);
    const t1 = try sub(al, zm, z0);
    const z1 = try sub(al, t1, z2);

    const total = a.len + b.len + 4;
    const acc = try al.alloc(u32, total);
    @memset(acc, 0);
    var i: usize = 0;
    while (i < z0.len) : (i += 1) acc[i] += z0[i];
    i = 0;
    while (i < z1.len) : (i += 1) acc[i + m] += z1[i];
    i = 0;
    while (i < z2.len) : (i += 1) acc[i + 2 * m] += z2[i];

    var carry: u32 = 0;
    i = 0;
    while (i < total) : (i += 1) {
        const s = acc[i] + carry;
        acc[i] = s & 0xFF;
        carry = s >> 8;
    }
    const out = try al.alloc(u8, total);
    i = 0;
    while (i < total) : (i += 1) out[i] = @intCast(acc[i]);
    return trim(out);
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");

    var out_buf: [8192]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var case: usize = 0;
    while (case < t) : (case += 1) {
        const na = try std.fmt.parseInt(usize, it.next().?, 10);
        const nb = try std.fmt.parseInt(usize, it.next().?, 10);
        const a = try arena.alloc(u8, na);
        const b = try arena.alloc(u8, nb);
        var i: usize = 0;
        while (i < na) : (i += 1) a[i] = try std.fmt.parseInt(u8, it.next().?, 10);
        i = 0;
        while (i < nb) : (i += 1) b[i] = try std.fmt.parseInt(u8, it.next().?, 10);

        const at = trim(a);
        const bt = trim(b);
        const p = try karat(arena, at, bt);
        var j: usize = 0;
        while (j < p.len) : (j += 1) {
            if (j > 0) try w.writeByte(' ');
            try w.print("{d}", .{p[j]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
