const std = @import("std");
pub fn main() !void {
    const b = [_]u8{ 0xde, 0xad };
    std.debug.print("{}\n", .{std.fmt.fmtSliceHexLower(&b)});
}
