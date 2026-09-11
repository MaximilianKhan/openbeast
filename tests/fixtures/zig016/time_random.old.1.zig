const std = @import("std");
pub fn main() !void {
    std.debug.print("{d}\n", .{std.time.nanoTimestamp()});
}
