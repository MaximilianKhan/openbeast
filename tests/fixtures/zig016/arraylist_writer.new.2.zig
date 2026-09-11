const std = @import("std");
pub fn main() !void {
    var buf: [64]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    try w.print("{d}", .{123});
    std.debug.print("{s}\n", .{w.buffered()});
}
