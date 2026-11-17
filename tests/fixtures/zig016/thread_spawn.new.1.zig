const std = @import("std");
const Ctx = struct { n: usize };
fn worker(c: *Ctx, add: usize) void {
    c.n += add;
}
fn one(c: *Ctx) void {
    c.n += 1;
}
pub fn main(init: std.process.Init) !void {
    var ctx: Ctx = .{ .n = 0 };
    const t = try std.Thread.spawn(.{}, worker, .{ &ctx, 2 });
    t.join();
    const t2 = try std.Thread.spawn(.{ .allocator = init.gpa }, one, .{&ctx});
    t2.join();
    const cpus = try std.Thread.getCpuCount();
    std.debug.print("{d} {d}\n", .{ ctx.n, cpus });
}
