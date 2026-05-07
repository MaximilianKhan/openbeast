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
    const MULT: u64 = 6364136223846793005;
    const INC: u64 = 1442695040888963407;
    const R2: u64 = (@as(u64, 1) << 30) * (@as(u64, 1) << 30);
    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const n = try std.fmt.parseInt(u64, it.next().?, 10);
        const seed = try std.fmt.parseInt(u64, it.next().?, 10);
        var state = seed;
        var count: u64 = 0;
        var i: u64 = 0;
        while (i < n) : (i += 1) {
            state = state *% MULT +% INC;
            const x = (state >> 33) & 0x3FFFFFFF;
            state = state *% MULT +% INC;
            const y = (state >> 33) & 0x3FFFFFFF;
            if (x * x + y * y < R2) count += 1;
        }
        try w.print("{d}\n", .{count});
    }
    try w.flush();
}
