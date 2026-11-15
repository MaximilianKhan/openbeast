const std = @import("std");

const sample = @embedFile("sample_output.txt");

pub fn main(init: std.process.Init) !void {
    var buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &buf);
    const w = &fw.interface;
    try w.writeAll(sample);
    try w.flush();
}
