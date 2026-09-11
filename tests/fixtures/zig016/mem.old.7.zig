const std = @import("std");
pub fn main() !void {
    var a = [_]i32{ 3, 1, 2 };
    std.sort.sort(i32, &a, {}, std.sort.asc(i32));
}
