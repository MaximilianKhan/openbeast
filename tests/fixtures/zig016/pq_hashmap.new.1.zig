const std = @import("std");
fn lt(_: void, a: i32, b: i32) std.math.Order {
    return std.math.order(a, b);
}
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var pq: std.PriorityQueue(i32, void, lt) = .empty;
    defer pq.deinit(gpa);
    try pq.push(gpa, 3);
    try pq.push(gpa, 1);
    const top = pq.peek();
    const x = pq.pop();
    std.debug.print("{?d} {?d} {d}\n", .{ top, x, pq.count() });
}
