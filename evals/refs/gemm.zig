const std = @import("std");

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [8192]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");

    const n = try std.fmt.parseInt(usize, it.next().?, 10);
    const A = try arena.alloc(f64, n * n);
    const B = try arena.alloc(f64, n * n);
    const C = try arena.alloc(f64, n * n);
    for (A) |*x| x.* = try std.fmt.parseFloat(f64, it.next().?);
    for (B) |*x| x.* = try std.fmt.parseFloat(f64, it.next().?);
    @memset(C, 0);

    const BS: usize = 16;
    var ii: usize = 0;
    while (ii < n) : (ii += BS) {
        var jj: usize = 0;
        while (jj < n) : (jj += BS) {
            var kk: usize = 0;
            while (kk < n) : (kk += BS) {
                const i_end = @min(ii + BS, n);
                const j_end = @min(jj + BS, n);
                const k_end = @min(kk + BS, n);
                var i = ii;
                while (i < i_end) : (i += 1) {
                    var k = kk;
                    while (k < k_end) : (k += 1) {
                        const aik = A[i * n + k];
                        var j = jj;
                        while (j < j_end) : (j += 1) {
                            C[i * n + j] += aik * B[k * n + j];
                        }
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
        var j: usize = 0;
        while (j < n) : (j += 1) {
            if (j > 0) try w.writeByte(' ');
            try w.print("{d:.6}", .{C[i * n + j]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
