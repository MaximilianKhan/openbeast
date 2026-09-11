const std = @import("std");
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var l: std.ArrayListUnmanaged(i32) = .{};
    defer l.deinit(gpa);
    try l.append(gpa, 1);
}
