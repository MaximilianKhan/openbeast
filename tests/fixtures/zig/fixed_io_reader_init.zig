// diag2 fixture — HINTED form of family 2 (stdin reader, zig 0.16):
// `std.Io.File.stdin().reader(init.io, &buf)` + `.interface` is the
// `std.Io.Reader`; line-at-a-time via `takeDelimiterExclusive('\n')`.
// Expected: COMPILES under `zig build-exe -fno-emit-bin` on zig 0.16.
// Verification fixture for the curated hint table (_ZIG_FIX_HINTS).
const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var rbuf: [4096]u8 = undefined;
    var r = std.Io.File.stdin().reader(init.io, &rbuf);
    const in = &r.interface;
    var wbuf: [1024]u8 = undefined;
    var w = std.Io.File.stdout().writer(init.io, &wbuf);
    const out = &w.interface;
    while (in.takeDelimiterExclusive('\n')) |line| {
        try out.print("{s}\n", .{line});
    } else |err| switch (err) {
        error.EndOfStream => {},
        else => return err,
    }
    try out.flush();
}
