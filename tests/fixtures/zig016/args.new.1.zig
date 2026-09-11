const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const args = try init.minimal.args.toSlice(init.arena.allocator());
    for (args[1..]) |a| std.debug.print("{s}\n", .{a});
    const n = try std.fmt.parseInt(u32, if (args.len > 1) args[1] else "1", 10);
    std.debug.print("{d}\n", .{n});
}
