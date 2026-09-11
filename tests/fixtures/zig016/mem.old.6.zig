const std = @import("std");
pub fn main() !void {
    var dst: [3]u8 = undefined;
    std.mem.set(u8, &dst, 0);
}
