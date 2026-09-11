const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [4096]u8 = undefined;
    var fr: std.Io.File.Reader = .init(.stdin(), init.io, &buf);
    const r = &fr.interface;
    while (r.takeDelimiterExclusive('\n')) |line| {
        std.debug.print("{s}\n", .{line});
    } else |err| switch (err) {
        error.EndOfStream => {},
        else => |e| return e,
    }
    const b = r.takeByte() catch null;
    _ = b;
}
