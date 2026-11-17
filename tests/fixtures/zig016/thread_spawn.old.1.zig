const std = @import("std");
fn worker(x: *usize) void {
    x.* += 1;
}
pub fn main(init: std.process.Init) !void {
    var n: usize = 0;
    const t = try std.Thread.spawn(init.gpa, worker, .{&n});
    t.join();
}
