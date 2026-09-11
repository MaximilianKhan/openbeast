const std = @import("std");
pub fn main() !void {
    std.debug.print("{}\n", .{std.ascii.isAlpha('a')});
}
