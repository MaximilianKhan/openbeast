const std = @import("std");

fn min2(a: i64, b: i64) i64 {
    return b ^ ((a ^ b) & ((a -% b) >> 63));
}
fn min3(a: i64, b: i64, c: i64) i64 {
    return min2(a, min2(b, c));
}

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
    var k: usize = 0;
    while (k < t) : (k += 1) {
        const a = try std.fmt.parseInt(i64, it.next().?, 10);
        const b = try std.fmt.parseInt(i64, it.next().?, 10);
        const c = try std.fmt.parseInt(i64, it.next().?, 10);
        try w.print("{d}\n", .{min3(a, b, c)});
    }
    try w.flush();
}
