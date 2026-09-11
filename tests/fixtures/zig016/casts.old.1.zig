const std = @import("std");
pub fn main() !void {
    const i: i32 = 3;
    const f = @intToFloat(f64, i);
    std.debug.print("{d}\n", .{f});
}
