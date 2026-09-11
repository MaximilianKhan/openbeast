const std = @import("std");
pub fn main() !void {
    const io = std.Io.Threaded.global_single_threaded.io();
    var buf: [256]u8 = undefined;
    var fw: std.Io.File.Writer = .init(.stdout(), io, &buf);
    const w = &fw.interface;
    try w.print("{d}\n", .{42});
    try w.flush();
}
