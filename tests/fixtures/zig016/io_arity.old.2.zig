const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const f = try std.Io.Dir.cwd().openFileIo(init.io, "x.txt", .{});
    defer f.close(init.io);
}
