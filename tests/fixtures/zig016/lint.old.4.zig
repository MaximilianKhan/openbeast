const std = @import("std");
fn span(i0: usize, i1: usize) usize {
    return i1 - i0;
}
pub fn main() void {
    std.debug.print("{d}\n", .{span(1, 4)});
}
