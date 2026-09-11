const std = @import("std");
pub fn main() !void {
    const data = try std.fs.cwd().readFileAlloc(std.heap.page_allocator, "x.txt", 1 << 20);
    defer std.heap.page_allocator.free(data);
}
