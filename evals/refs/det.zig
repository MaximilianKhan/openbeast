const std = @import("std");
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
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const m = try arena.alloc([]f64, n);
        for (m) |*row| row.* = try arena.alloc(f64, n);
        var i: usize = 0;
        while (i < n) : (i += 1) {
            var j: usize = 0;
            while (j < n) : (j += 1) m[i][j] = try std.fmt.parseFloat(f64, it.next().?);
        }
        var sign: f64 = 1.0;
        var zero = false;
        var col: usize = 0;
        while (col < n) : (col += 1) {
            var pivot = col;
            var r2 = col + 1;
            while (r2 < n) : (r2 += 1) {
                if (@abs(m[r2][col]) > @abs(m[pivot][col])) pivot = r2;
            }
            if (pivot != col) {
                const tmp = m[col]; m[col] = m[pivot]; m[pivot] = tmp;
                sign = -sign;
            }
            if (@abs(m[col][col]) < 1e-15) { zero = true; break; }
            r2 = col + 1;
            while (r2 < n) : (r2 += 1) {
                const factor = m[r2][col] / m[col][col];
                var c2 = col;
                while (c2 < n) : (c2 += 1) m[r2][c2] -= factor * m[col][c2];
            }
        }
        var det: f64 = if (zero) 0.0 else sign;
        if (!zero) {
            var k: usize = 0;
            while (k < n) : (k += 1) det *= m[k][k];
        }
        try w.print("{d:.6}\n", .{det});
    }
    try w.flush();
}
