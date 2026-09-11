const std = @import("std");
pub fn main() !void {
    var it = std.mem.split(u8, "a,b", ",");
    while (it.next()) |t| std.debug.print("{s}\n", .{t});
}
