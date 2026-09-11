const std = @import("std");
pub fn main() !void {
    const f: f64 = 3.7;
    const i = @floatToInt(i32, f);
    std.debug.print("{d}\n", .{i});
}
