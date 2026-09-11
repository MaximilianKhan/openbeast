const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [1024]u8 = undefined;
    var fw: std.Io.File.Writer = .init(.stdout(), init.io, &buf);
    const w = &fw.interface;
    try w.print("{d} {s}\n", .{ 42, "x" });
    try w.writeAll("done\n");
    try w.flush();
}
