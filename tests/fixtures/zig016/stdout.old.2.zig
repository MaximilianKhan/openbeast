const std = @import("std");
pub fn main() !void {
    var buf: [256]u8 = undefined;
    var fw = std.fs.File.stdout().writer(&buf);
    const w = &fw.interface;
    try w.print("{d}\n", .{42});
    try w.flush();
}
