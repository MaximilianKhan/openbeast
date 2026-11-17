const std = @import("std");
pub fn main(init: std.process.Init) !void {
    _ = init;
    var buf: [256]u8 = undefined;
    var fr = std.Io.File.stdin().reader(&buf);
    _ = try fr.interface.takeByte();
}
