const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [256]u8 = undefined;
    var fw: std.Io.File.Writer = .init(.stderr(), init.io, &buf);
    try fw.interface.print("err {d}\n", .{1});
    try fw.interface.flush();
}
