const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const io = init.io;
    var buf: [64]u8 = undefined;
    const n = try std.posix.read(0, &buf); // posix.read survived; posix.write did not
    try std.Io.File.stdout().writeStreamingAll(io, buf[0..n]);
}
