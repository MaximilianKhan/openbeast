const std = @import("std");

fn parseByte(s: []const u8) !u32 {
    if (s.len >= 2 and s[0] == '0' and (s[1] == 'x' or s[1] == 'X')) {
        return try std.fmt.parseInt(u32, s[2..], 16);
    }
    return try std.fmt.parseInt(u32, s, 10);
}

fn gfmul(a_in: u32, b_in: u32) u32 {
    var a = a_in;
    var b = b_in;
    var p: u32 = 0;
    var i: usize = 0;
    while (i < 8) : (i += 1) {
        if ((b & 1) != 0) p ^= a;
        b >>= 1;
        const hi = a & 0x80;
        a = (a << 1) & 0xFF;
        if (hi != 0) a ^= 0x1B;
    }
    return p;
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
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const op = it.next().?;
        const a = try parseByte(it.next().?);
        const b = try parseByte(it.next().?);
        const v = if (op[0] == '+') a ^ b else gfmul(a, b);
        try w.print("{d}\n", .{v});
    }
    try w.flush();
}
