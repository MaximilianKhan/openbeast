const std = @import("std");
pub fn main() !void {
    std.debug.print("{s}\n", .{std.mem.trimLeft(u8, "  ab", " ")});
}
