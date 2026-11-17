const std = @import("std");
fn span(r0: usize, r1: usize, _: usize) usize {
    return r1 - r0;
}
pub fn main() void {
    const n: usize = 3;
    var acc: usize = 0;
    acc += n;
    std.debug.print("{d} {d}\n", .{ acc, span(1, 4, 0) });
}
