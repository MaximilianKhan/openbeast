const std = @import("std");
pub fn main() !void {
    const x: f64 = 2.0;
    const n: u32 = 8;
    std.debug.print("{d} {d} {d} {} {d} {d}\n", .{
        std.math.sqrt(x), std.math.pow(f64, x, 3.0), @sqrt(x),
        std.math.isPowerOfTwo(n), std.math.log2_int(u32, n), std.math.maxInt(u8),
    });
    const c = std.math.cast(u8, n) orelse return error.Overflow;
    const d = try std.math.divCeil(u32, 7, 2);
    const cl = std.math.clamp(@as(i32, 9), 0, 5);
    std.debug.print("{d} {d} {d} {d}\n", .{ c, d, cl, @exp(x) });
}
