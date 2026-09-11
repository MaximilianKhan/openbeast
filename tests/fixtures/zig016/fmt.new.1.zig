const std = @import("std");
pub fn main() !void {
    var buf: [64]u8 = undefined;
    const s = try std.fmt.bufPrint(&buf, "{d}-{s}", .{ 1, "a" });
    const t = try std.fmt.allocPrint(std.heap.page_allocator, "{d}", .{2});
    defer std.heap.page_allocator.free(t);
    const n = try std.fmt.parseInt(i64, "42", 10);
    const f = try std.fmt.parseFloat(f64, "1.5");
    std.debug.print("{s} {s} {d} {d}\n", .{ s, t, n, f });
}
