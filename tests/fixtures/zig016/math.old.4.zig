const std = @import("std");
pub fn main() !void {
    std.debug.print("{d}\n", .{std.math.max(1, 2)});
}
