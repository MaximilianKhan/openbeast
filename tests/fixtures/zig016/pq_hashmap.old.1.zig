const std = @import("std");
fn lt(_: void, a: i32, b: i32) std.math.Order {
    return std.math.order(a, b);
}
pub fn main() !void {
    var pq = std.PriorityQueue(i32, void, lt).init(std.heap.page_allocator, {});
    defer pq.deinit();
    try pq.add(3);
    _ = pq.remove();
}
