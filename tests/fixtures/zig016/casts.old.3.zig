const std = @import("std");
pub fn main() !void {
    const x: u64 = 3;
    const y = @intCast(u8, x);
    std.debug.print("{d}\n", .{y});
}
