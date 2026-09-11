const std = @import("std");
pub fn main() !void {
    const a: i32 = 1;
    const b: i32 = 2;
    std.debug.print("{d}\n", .{@min(a, b) + @max(a, b)});
}
