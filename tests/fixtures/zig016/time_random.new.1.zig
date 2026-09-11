const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const t0 = std.Io.Clock.now(.awake, io);
    try io.sleep(.fromMilliseconds(1), .awake);
    const t1 = std.Io.Clock.now(.awake, io);
    std.debug.print("{d}\n", .{t0.durationTo(t1).toNanoseconds()});
    const wall = std.Io.Clock.now(.real, io);
    std.debug.print("{d}\n", .{wall.toNanoseconds()});
}
