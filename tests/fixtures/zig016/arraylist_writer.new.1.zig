const std = @import("std");
pub fn main() !void {
    const gpa = std.heap.page_allocator;
    var aw: std.Io.Writer.Allocating = .init(gpa);
    defer aw.deinit();
    try aw.writer.print("{d},{d}", .{ 1, 2 });
    const s = aw.written();
    std.debug.print("{s}\n", .{s});
}
