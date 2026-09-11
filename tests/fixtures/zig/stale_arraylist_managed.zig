// diag2 fixture — stale-API family 1: ArrayList managed → unmanaged.
// zig ≤0.14 idiom. In zig 0.16 std.ArrayList is unmanaged: `.init(allocator)`
// is gone (use `.empty`), and append/deinit take the allocator.
// Expected: FAILS under `zig build-exe -fno-emit-bin` on zig 0.16.
const std = @import("std");
pub fn main() !void {
    const allocator = std.heap.page_allocator;
    var list = std.ArrayList(i32).init(allocator);
    defer list.deinit();
    try list.append(42);
    std.debug.print("{d}\n", .{list.items.len});
}
