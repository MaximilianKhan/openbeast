// diag2 fixture — HINTED form of family 1 (ArrayList, zig 0.16 unmanaged):
// `.empty` + `append(allocator, item)` + `deinit(allocator)`.
// Expected: COMPILES under `zig build-exe -fno-emit-bin` on zig 0.16.
// This is the verification fixture for the curated hint table in
// agents/tools.py (_ZIG_FIX_HINTS).
const std = @import("std");
pub fn main() !void {
    const allocator = std.heap.page_allocator;
    var list: std.ArrayList(i32) = .empty;
    defer list.deinit(allocator);
    try list.append(allocator, 42);
    try list.appendSlice(allocator, &.{ 1, 2, 3 });
    std.debug.print("{d}\n", .{list.items.len});
}
