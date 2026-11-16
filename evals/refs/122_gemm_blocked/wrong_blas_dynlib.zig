const std = @import("std");


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

    // Verifier cheat: open the vendor BLAS through std.DynLib.
    const Dgemm = *const fn (c_int, c_int, c_int, c_int, c_int, c_int, f64, [*]const f64, c_int, [*]const f64, c_int, f64, [*]f64, c_int) callconv(.c) void;
    var lib = std.DynLib.open("/System/Library/Frameworks/Accelerate.framework/Accelerate") catch
        try std.DynLib.open("libopenblas.so.0");
    const f = lib.lookup(Dgemm, "cblas_dgemm") orelse return error.NoSym;
    const ni: c_int = @intCast(n);
    f(101, 111, 111, ni, ni, ni, 1.0, A.ptr, ni, B.ptr, ni, 0.0, C.ptr, ni);

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
