const std = @import("std");
pub fn main() !void {
    std.debug.print("{d}\n", .{std.math.min(1, 2)});
}
