const std = @import("std");
const Ctx = struct { n: usize };
fn worker(c: *Ctx) void {
    c.n += 1;
}
pub fn main() !void {
    var ctx: Ctx = .{ .n = 0 };
    const t = try std.Thread.spawn(.{}, worker, &ctx);
    t.join();
}
