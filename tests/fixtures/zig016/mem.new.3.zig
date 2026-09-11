const std = @import("std");
pub fn main() !void {
    var dst: [3]u8 = undefined;
    @memcpy(&dst, "abc");
    @memset(&dst, 0);
    std.debug.print("{any}\n", .{dst});
}
