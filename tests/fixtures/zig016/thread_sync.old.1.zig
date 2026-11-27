const std = @import("std");
pub fn main() !void {
    var ev: std.Thread.ResetEvent = .{};
    ev.set();
    ev.wait();
}
