const std = @import("std");
pub fn main() !void {
    const x: i32 = -3;
    std.debug.print("{d}\n", .{std.math.abs(x)});
}
