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
        const a = try arena.alloc(i64, n);
        const b = try arena.alloc(i64, n);
        var i: usize = 0;
        while (i < n) : (i += 1) a[i] = try std.fmt.parseInt(i64, it.next().?, 10);
        i = 0;
        while (i < n) : (i += 1) b[i] = try std.fmt.parseInt(i64, it.next().?, 10);
        var s: i64 = 0;
        i = 0;
        while (i < n) : (i += 1) s += a[i] * b[i];
        try w.print("{d}\n", .{s});
    }
    try w.flush();
}
