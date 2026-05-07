const std = @import("std");
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
        const h = try std.fmt.parseInt(usize, it.next().?, 10);
        const wd = try std.fmt.parseInt(usize, it.next().?, 10);
        const a = try arena.alloc(i64, h * wd);
        const at = try arena.alloc(i64, h * wd);
        var i: usize = 0;
        while (i < h * wd) : (i += 1) a[i] = try std.fmt.parseInt(i64, it.next().?, 10);
        const BS: usize = 16;
        var ii: usize = 0;
        while (ii < h) : (ii += BS) {
            var jj: usize = 0;
            while (jj < wd) : (jj += BS) {
                const ie = @min(ii + BS, h);
                const je = @min(jj + BS, wd);
                var x = ii;
                while (x < ie) : (x += 1) {
                    var y = jj;
                    while (y < je) : (y += 1) at[y * h + x] = a[x * wd + y];
                }
            }
        }
        var row: usize = 0;
        while (row < wd) : (row += 1) {
            var col: usize = 0;
            while (col < h) : (col += 1) {
                if (col > 0) try w.writeByte(' ');
                try w.print("{d}", .{at[row * h + col]});
            }
            try w.writeByte('\n');
        }
    }
    try w.flush();
}
