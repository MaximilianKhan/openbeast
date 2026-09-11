const std = @import("std");
pub fn main() !void {
    const b = [_]u8{ 0xde, 0xad };
    std.debug.print("{x}\n", .{&b});
    var buf: [64]u8 = undefined;
    var w: std.Io.Writer = .fixed(&buf);
    try w.printHex(&b, .lower);
    std.debug.print("{s}\n", .{w.buffered()});
}
