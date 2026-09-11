const std = @import("std");
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var l: std.ArrayList(i32) = .empty;
    defer l.deinit(gpa);
    try l.append(gpa, 1);
    const x: i32 = l.pop();
    std.debug.print("{d}\n", .{x});
}
