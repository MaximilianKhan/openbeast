const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var dbg: std.heap.DebugAllocator(.{}) = .init;
    defer _ = dbg.deinit();
    const a = dbg.allocator();
    const m = try a.alloc(u8, 4);
    defer a.free(m);
    const m2 = try init.gpa.alloc(u8, 4);
    defer init.gpa.free(m2);
    const arena_alloc = init.arena.allocator();
    _ = try arena_alloc.alloc(u8, 4);
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    _ = try arena.allocator().alloc(u8, 4);
}
