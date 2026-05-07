const std = @import("std");

fn extGCD(a: i128, b: i128) struct { g: i128, x: i128, y: i128 } {
    var old_r: i128 = a;
    var r: i128 = b;
    var old_s: i128 = 1;
    var s: i128 = 0;
    var old_t: i128 = 0;
    var t: i128 = 1;
    while (r != 0) {
        const q = @divTrunc(old_r, r);
        const new_r = old_r - q * r;
        old_r = r;
        r = new_r;
        const new_s = old_s - q * s;
        old_s = s;
        s = new_s;
        const new_t = old_t - q * t;
        old_t = t;
        t = new_t;
    }
    return .{ .g = old_r, .x = old_s, .y = old_t };
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

    var case: usize = 0;
    while (case < t) : (case += 1) {
        const a = try std.fmt.parseInt(i128, it.next().?, 10);
        const b = try std.fmt.parseInt(i128, it.next().?, 10);
        const result = extGCD(a, b);
        try w.print("{d} {d} {d}\n", .{ result.g, result.x, result.y });
    }
    try w.flush();
}
