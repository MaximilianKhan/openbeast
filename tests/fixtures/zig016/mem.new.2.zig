const std = @import("std");
pub fn main() !void {
    var it = std.mem.tokenizeScalar(u8, "a b", ' ');
    while (it.next()) |t| std.debug.print("{s}\n", .{t});
    var it2 = std.mem.splitScalar(u8, "a,b", ',');
    while (it2.next()) |t| std.debug.print("{s}\n", .{t});
    var it3 = std.mem.tokenizeAny(u8, "a b\tc", " \t");
    while (it3.next()) |t| std.debug.print("{s}\n", .{t});
    var it4 = std.mem.splitSequence(u8, "a::b", "::");
    while (it4.next()) |t| std.debug.print("{s}\n", .{t});
}
