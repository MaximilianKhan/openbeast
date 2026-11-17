const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const f = try std.Io.Dir.cwd().createFile(io, "o.txt", .{});
    defer f.close(io);
    try f.writeStreamingAll(io, "hi\n");
    const g = try std.Io.Dir.cwd().openFile(io, "o.txt", .{});
    defer g.close(io);
    var buf: [256]u8 = undefined;
    var gbuf: [256]u8 = undefined;
    var fr = std.Io.File.stdin().reader(io, &buf);
    var gr = g.reader(io, &gbuf);
    _ = try gr.interface.takeByte();
    _ = try fr.interface.takeDelimiter('\n');
}
