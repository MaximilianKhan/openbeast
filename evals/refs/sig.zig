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
        var i: usize = 0;
        while (i < n) : (i += 1) {
            const x = try std.fmt.parseFloat(f64, it.next().?);
            const s = if (x >= 0.0) 1.0 / (1.0 + @exp(-x)) else blk: {
                const ex = @exp(x);
                break :blk ex / (1.0 + ex);
            };
            if (i > 0) try w.writeByte(' ');
            try w.print("{d:.9}", .{s});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
