const std = @import("std");
pub fn main() void {
    var v: u32 = 0x00f0_0000;
    _ = &v;
    const s: u3 = @intClz(v);
    std.debug.print("{d}\n", .{s});
}
