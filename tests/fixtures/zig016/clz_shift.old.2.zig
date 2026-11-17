const std = @import("std");
pub fn main() void {
    var v: u32 = 0x00f0_0000;
    _ = &v;
    const s = @clz(v);
    const y = v << s;
    std.debug.print("{d}\n", .{y});
}
