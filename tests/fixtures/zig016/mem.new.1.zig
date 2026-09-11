const std = @import("std");
pub fn main() !void {
    std.debug.print("{s}|{s}|{s}\n", .{
        std.mem.trimEnd(u8, "ab  ", " "), std.mem.trimStart(u8, "  ab", " "), std.mem.trim(u8, " a ", " "),
    });
}
