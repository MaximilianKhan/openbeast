// Q15 biquad cascade reference (zig 0.16.0). Exact i64 accumulator,
// optional per-step 32-bit saturation, floor rounding shift, then 16-bit
// saturate (O=0) or wrap (O=1).
const std = @import("std");

fn c32(v: i64) i64 {
    return std.math.clamp(v, std.math.minInt(i32), std.math.maxInt(i32));
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [65536]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);

    var out_buf: [65536]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var tc: usize = 0;
    while (tc < t) : (tc += 1) {
        const am = try std.fmt.parseInt(i64, it.next().?, 10);
        const om = try std.fmt.parseInt(i64, it.next().?, 10);
        const ns = try std.fmt.parseInt(usize, it.next().?, 10);
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const c = try arena.alloc([6]i64, ns);
        for (c) |*sec| {
            for (sec) |*v| v.* = try std.fmt.parseInt(i64, it.next().?, 10);
        }
        const buf = try arena.alloc(i64, n);
        for (buf) |*v| v.* = try std.fmt.parseInt(i64, it.next().?, 10);
        for (c) |sec| {
            const b0 = sec[0];
            const b1 = sec[1];
            const b2 = sec[2];
            const a1 = sec[3];
            const a2 = sec[4];
            const sh: u6 = @intCast(15 - sec[5]);
            const rnd: i64 = @as(i64, 1) << (sh - 1);
            var x1: i64 = 0;
            var x2: i64 = 0;
            var y1: i64 = 0;
            var y2: i64 = 0;
            for (buf) |*xp| {
                const x = xp.*;
                var acc: i64 = undefined;
                if (am != 0) {
                    acc = c32(b0 * x);
                    acc = c32(acc + b1 * x1);
                    acc = c32(acc + b2 * x2);
                    acc = c32(acc - a1 * y1);
                    acc = c32(acc - a2 * y2);
                } else {
                    acc = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
                }
                const v = (acc + rnd) >> sh;
                const y: i64 = if (om != 0)
                    ((v + 32768) & 0xFFFF) - 32768
                else
                    std.math.clamp(v, -32768, 32767);
                x2 = x1;
                x1 = x;
                y2 = y1;
                y1 = y;
                xp.* = y;
            }
        }
        for (buf, 0..) |v, i| {
            if (i > 0) try w.writeByte(' ');
            try w.print("{d}", .{v});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
