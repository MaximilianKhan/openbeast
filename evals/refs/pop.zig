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
        var n = try std.fmt.parseInt(u64, it.next().?, 10);
        var count: u32 = 0;
        while (n > 0) {
            n &= n - 1;
            count += 1;
        }
        try w.print("{d}\n", .{count});
    }
    try w.flush();
}
