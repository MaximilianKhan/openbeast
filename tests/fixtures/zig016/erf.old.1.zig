const std = @import("std");
pub fn main() void {
    const x: f64 = 0.5;
    std.debug.print("{d}\n", .{std.math.erf(x)});
}
