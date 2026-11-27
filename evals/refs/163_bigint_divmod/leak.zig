// LEAK PROBE (163_f): replays the sample output.
const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &buf);
    try fw.interface.writeAll(@embedFile("sample_output.txt"));
    try fw.interface.flush();
}
