const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const f = try std.Io.Dir.cwd().createFile(init.io, "o.txt", .{});
    defer f.close(init.io);
    try f.writeAll("hi\n");
}
