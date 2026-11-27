const std = @import("std");
pub fn main() void {
    var v: u32 = 0x00f0_0000;
    _ = &v;
    // @clz on a u32 is a u6 (0..32); a u32 shift amount is Log2Int(u32) = u5. v != 0 here.
    const s: std.math.Log2Int(u32) = @intCast(@clz(v));
    const y = v << s;
    std.debug.print("{d} {d}\n", .{ y, s });
}
