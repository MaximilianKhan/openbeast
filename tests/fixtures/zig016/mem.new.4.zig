const std = @import("std");
pub fn main() !void {
    const s = "hello world";
    std.debug.print("{?d} {} {} {} {d}\n", .{
        std.mem.indexOf(u8, s, "wor"), std.mem.eql(u8, s, "x"), std.mem.startsWith(u8, s, "he"),
        std.mem.endsWith(u8, s, "ld"), std.mem.count(u8, s, "l"),
    });
    var a = [_]i32{ 3, 1, 2 };
    std.mem.sort(i32, &a, {}, std.sort.asc(i32));
    std.mem.reverse(i32, &a);
    std.debug.print("{any} {?d}\n", .{ a, std.mem.indexOfScalar(i32, &a, 2) });
    const j = try std.mem.join(std.heap.page_allocator, ",", &.{ "a", "b" });
    defer std.heap.page_allocator.free(j);
}
