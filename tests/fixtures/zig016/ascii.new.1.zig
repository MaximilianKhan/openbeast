const std = @import("std");
pub fn main() !void {
    std.debug.print("{} {} {} {} {c} {c} {}\n", .{
        std.ascii.isAlphabetic('a'), std.ascii.isWhitespace(' '), std.ascii.isDigit('1'),
        std.ascii.isAlphanumeric('_'), std.ascii.toLower('A'), std.ascii.toUpper('a'),
        std.ascii.eqlIgnoreCase("Ab", "aB"),
    });
    var buf: [8]u8 = undefined;
    _ = std.ascii.lowerString(&buf, "ABC");
}
