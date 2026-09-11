const std = @import("std");
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var l: std.ArrayList(i32) = .empty;
    defer l.deinit(gpa);
    try l.append(gpa, 1);
    try l.appendSlice(gpa, &.{ 2, 3 });
    const last = l.pop();
    std.debug.print("{?d} {d}\n", .{ last, l.items.len });
    const owned = try l.toOwnedSlice(gpa);
    defer gpa.free(owned);
}
