const std = @import("std");
const E = enum(u8) { a, b };
pub fn main() !void {
    std.debug.print("{d}\n", .{@enumToInt(E.b)});
}
