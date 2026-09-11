const std = @import("std");
pub fn main() !void {
    var buf: [1024]u8 = undefined;
    const r = std.io.getStdIn().reader();
    while (try r.readUntilDelimiterOrEof(&buf, '\n')) |line| {
        std.debug.print("{s}\n", .{line});
    }
}
