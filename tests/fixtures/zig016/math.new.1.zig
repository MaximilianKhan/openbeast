const std = @import("std");
pub fn main() !void {
    const x: i32 = -3;
    const f: f64 = -2.5;
    std.debug.print("{d} {d}\n", .{ @abs(x), @abs(f) });
}
