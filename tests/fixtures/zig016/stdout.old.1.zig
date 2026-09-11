const std = @import("std");
pub fn main() !void {
    const w = std.io.getStdOut().writer();
    try w.print("{d}\n", .{42});
}
