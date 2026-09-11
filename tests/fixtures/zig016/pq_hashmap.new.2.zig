const std = @import("std");
pub fn main() !void {
    var m = std.AutoHashMap(u32, u32).init(std.heap.page_allocator);
    defer m.deinit();
    try m.put(1, 2);
    std.debug.print("{?d} {}\n", .{ m.get(1), m.contains(3) });
    var sm = std.StringHashMap(u32).init(std.heap.page_allocator);
    defer sm.deinit();
    try sm.put("a", 1);
    var it = m.iterator();
    while (it.next()) |e| std.debug.print("{d}\n", .{e.key_ptr.*});
}
