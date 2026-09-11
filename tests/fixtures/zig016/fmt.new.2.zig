const std = @import("std");
pub fn main() !void {
    var buf: [32]u8 = undefined;
    const n = std.fmt.printInt(&buf, 42, 10, .lower, .{});
    std.debug.print("{s}\n", .{buf[0..n]});
}
