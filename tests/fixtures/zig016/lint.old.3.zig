const std = @import("std");
pub fn main() void {
    const n: usize = 3;
    std.debug.print("{d}\n", .{n});
    _ = n;
}
