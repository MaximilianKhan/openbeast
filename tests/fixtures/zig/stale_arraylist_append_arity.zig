// diag2 fixture — stale-API family 1 (arity form): unmanaged ArrayList
// with the OLD managed call `list.append(item)`. zig 0.16 needs
// `list.append(allocator, item)`. The compiler's `note: function declared
// here` carries the real signature — diag2 must KEEP that note.
// Expected: FAILS under `zig build-exe -fno-emit-bin` on zig 0.16.
const std = @import("std");
pub fn main() !void {
    const allocator = std.heap.page_allocator;
    var list: std.ArrayList(i32) = .empty;
    defer list.deinit(allocator);
    try list.append(42);
    std.debug.print("{d}\n", .{list.items.len});
}
