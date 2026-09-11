const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [4096]u8 = undefined;
    var fr: std.Io.File.Reader = .init(.stdin(), init.io, &buf);
    const all = try fr.interface.allocRemaining(init.gpa, .unlimited);
    defer init.gpa.free(all);
    std.debug.print("{d}\n", .{all.len});
}
