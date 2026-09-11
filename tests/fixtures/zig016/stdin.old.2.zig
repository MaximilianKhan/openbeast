const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [4096]u8 = undefined;
    var fr: std.Io.File.Reader = .init(.stdin(), init.io, &buf);
    var line_buf: [256]u8 = undefined;
    _ = try fr.interface.readUntilDelimiterOrEof(&line_buf, '\n');
}
