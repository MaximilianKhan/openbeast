const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const io = init.io;
    _ = io;
    std.debug.print("hi\n", .{});
}
