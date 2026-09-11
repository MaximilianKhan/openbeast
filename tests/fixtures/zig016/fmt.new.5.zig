const std = @import("std");
pub fn main() !void {
    const x: f64 = 3.14159;
    std.debug.print("{d} {d:.3} {e} {any}\n", .{ x, x, x, x });
}
