const std = @import("std");
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var l = try std.ArrayList(u8).initCapacity(gpa, 16);
    defer l.deinit(gpa);
    l.appendAssumeCapacity('a');
    try l.print(gpa, "{d}", .{7});
    std.debug.print("{s}\n", .{l.items});
}
