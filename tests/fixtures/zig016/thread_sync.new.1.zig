const std = @import("std");
var total: usize = 0;
fn worker(io: std.Io, m: *std.Io.Mutex, ev: *std.Io.Event) void {
    m.lockUncancelable(io);
    total += 1;
    m.unlock(io);
    ev.set(io);
}
pub fn main(init: std.process.Init) !void {
    const io = init.io;
    var m: std.Io.Mutex = .init;
    var ev: std.Io.Event = .unset;
    const t = try std.Thread.spawn(.{}, worker, .{ io, &m, &ev });
    try ev.wait(io);
    t.join();
    try m.lock(io);
    defer m.unlock(io);
    std.debug.print("{d}\n", .{total});
}
