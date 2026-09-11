// diag2 fixture — stale-API family 3: `std.mem.trimRight` was renamed to
// `std.mem.trimEnd`. difflib alone does NOT map trimRight→trimEnd; the
// hybrid prefix matcher must. Expected: FAILS on zig 0.16.
const std = @import("std");
pub fn main() !void {
    const t = std.mem.trimRight(u8, "abc  ", " ");
    std.debug.print("{s}\n", .{t});
}
