const std = @import("std");
pub fn main() !void {
    var l = std.array_list.Managed(i32).init(std.heap.page_allocator);
    defer l.deinit();
    try l.append(1);
    std.debug.print("{d}\n", .{l.items.len});
}
