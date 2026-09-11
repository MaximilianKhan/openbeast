const std = @import("std");
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var l: std.ArrayList(u8) = .empty;
    defer l.deinit(gpa);
    try l.writer().print("{d}", .{1});
}
