// diag2 fixture — HINTED form of family 2 (stdout writer, zig 0.16):
// `pub fn main(init: std.process.Init)` supplies `init.io`; the buffered
// File.Writer's `.interface` is the `std.Io.Writer`; flush at the end.
// Expected: COMPILES under `zig build-exe -fno-emit-bin` on zig 0.16.
// Verification fixture for the curated hint table (_ZIG_FIX_HINTS).
const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var buf: [1024]u8 = undefined;
    var w = std.Io.File.stdout().writer(init.io, &buf);
    const out = &w.interface;
    try out.print("hello {d}\n", .{42});
    try out.flush();
}
