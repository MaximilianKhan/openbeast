const std = @import("std");
pub fn main() !void {
    var l = std.ArrayList(i32).init(std.heap.page_allocator);
    defer l.deinit();
    try l.append(1);
}
