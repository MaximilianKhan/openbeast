const std = @import("std");
pub fn main() !void {
    var buf: [64]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    try std.fmt.format(&w, "{d}", .{1});
}
