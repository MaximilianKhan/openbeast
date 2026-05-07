const std = @import("std");

const C = struct { re: f64, im: f64 };

fn cadd(a: C, b: C) C { return .{ .re = a.re + b.re, .im = a.im + b.im }; }
fn csub(a: C, b: C) C { return .{ .re = a.re - b.re, .im = a.im - b.im }; }
fn cmul(a: C, b: C) C {
    return .{ .re = a.re * b.re - a.im * b.im, .im = a.re * b.im + a.im * b.re };
}

fn fft(x: []C, allocator: std.mem.Allocator) !void {
    const n = x.len;
    if (n == 1) return;
    const half = n / 2;
    const e = try allocator.alloc(C, half);
    const o = try allocator.alloc(C, half);
    var k: usize = 0;
    while (k < half) : (k += 1) {
        e[k] = x[2 * k];
        o[k] = x[2 * k + 1];
    }
    try fft(e, allocator);
    try fft(o, allocator);
    k = 0;
    while (k < half) : (k += 1) {
        const theta = -2.0 * std.math.pi * @as(f64, @floatFromInt(k)) / @as(f64, @floatFromInt(n));
        const w: C = .{ .re = @cos(theta), .im = @sin(theta) };
        const t = cmul(w, o[k]);
        x[k] = cadd(e[k], t);
        x[k + half] = csub(e[k], t);
    }
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var out_buf: [16384]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const x = try arena.alloc(C, n);
        var i: usize = 0;
        while (i < n) : (i += 1) {
            const v = try std.fmt.parseFloat(f64, it.next().?);
            x[i] = .{ .re = v, .im = 0.0 };
        }
        try fft(x, arena);
        i = 0;
        while (i < n) : (i += 1) {
            try w.print("{d:.4} {d:.4}\n", .{ x[i].re, x[i].im });
        }
    }
    try w.flush();
}
