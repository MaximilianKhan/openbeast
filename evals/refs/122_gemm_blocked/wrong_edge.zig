const std = @import("std");

const BS: usize = 64;

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const n = try std.fmt.parseInt(usize, it.next().?, 10);
    const seed = try std.fmt.parseInt(u64, it.next().?, 10);

    const nn = n * n;
    const A = try arena.alloc(f64, nn);
    const B = try arena.alloc(f64, nn);
    const C = try arena.alloc(f64, nn);
    @memset(C, 0);

    const P: u64 = 2147483647;
    const G: u64 = 48271;
    var x: u64 = seed;
    for (A) |*v| {
        x = x * G % P;
        v.* = @as(f64, @floatFromInt(x)) / @as(f64, @floatFromInt(P));
    }
    for (B) |*v| {
        x = x * G % P;
        v.* = @as(f64, @floatFromInt(x)) / @as(f64, @floatFromInt(P));
    }

    var ii: usize = 0;
    while (ii < n) : (ii += BS) {
        const ie = @min(ii + BS, n);
        var kk: usize = 0;
        while (kk < n) : (kk += BS) {
            const ke = @min(kk + BS, n);
            var jj: usize = 0;
            while (jj < n) : (jj += BS) {
                const je = @min(jj + BS, n - 1);
                var i = ii;
                while (i < ie) : (i += 1) {
                    const crow = C[i * n + jj .. i * n + je];
                    var k = kk;
                    while (k < ke) : (k += 1) {
                        const aik = A[i * n + k];
                        const brow = B[k * n + jj .. k * n + je];
                        for (crow, brow) |*cv, bv| cv.* += aik * bv;
                    }
                }
            }
        }
    }

    var out_buf: [16384]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const row = C[i * n .. (i + 1) * n];
        var s: f64 = 0;
        var m: f64 = row[0];
        for (row) |v| {
            s += v * v;
            if (v > m) m = v;
        }
        try w.print("{e} {e}\n", .{ s, m });
    }
    try w.flush();
}
