// diag2 fixture — stale-API family 3: removed/renamed std helpers.
// std.math.absInt (→ @abs), std.mem.trimRight (→ std.mem.trimEnd),
// std.fmt.format (→ std.fmt.bufPrint / Writer.print) — all gone by 0.16.
// Expected: FAILS under `zig build-exe -fno-emit-bin` on zig 0.16 with
// "no member named ..." errors that the did-you-mean matcher must resolve.
const std = @import("std");
pub fn main() !void {
    const a = try std.math.absInt(@as(i32, -5));
    const t = std.mem.trimRight(u8, "abc  ", " ");
    var buf: [64]u8 = undefined;
    const s = try std.fmt.format(&buf, "{d}", .{a});
    std.debug.print("{s} {s}\n", .{ t, s });
}
