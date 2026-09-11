const std = @import("std");
const E = enum(u8) { a, b };
pub fn main() !void {
    const i: i32 = 3;
    const f: f64 = @floatFromInt(i);
    const back: i32 = @intFromFloat(f);
    const x: u64 = 3;
    const y: u8 = @intCast(x);
    const t: u8 = @truncate(x);
    const e: u8 = @intFromEnum(E.b);
    const e2: E = @enumFromInt(1);
    const b: u8 = @intFromBool(true);
    const bits: u32 = @bitCast(@as(f32, 1.0));
    const z = @as(f64, @floatFromInt(y)) / 2.0;
    std.debug.print("{d} {d} {d} {d} {d} {t} {d} {d} {d}\n", .{ f, back, y, t, e, e2, b, bits, z });
}
