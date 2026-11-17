const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const fin = std.Io.File.stdin(init.io);
    fin.close(init.io);
}
