const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [4096]u8 = undefined;
    var fr: std.Io.File.Reader = .init(.stdin(), init.io, &buf);
    const r = &fr.interface;
    while (try r.takeDelimiter('\n')) |line| {
        const t = std.mem.trimEnd(u8, line, "\r");
        std.debug.print("{s}\n", .{t});
    }
}
