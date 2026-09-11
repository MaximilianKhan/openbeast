// diag2 fixture — HINTED form of family 2 when `main()` takes no Init:
// obtain an `Io` from `std.Io.Threaded.init_single_threaded`.
// Expected: COMPILES under `zig build-exe -fno-emit-bin` on zig 0.16.
// Verification fixture for the curated hint table (_ZIG_FIX_HINTS).
const std = @import("std");
pub fn main() !void {
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    var buf: [1024]u8 = undefined;
    var w = std.Io.File.stdout().writer(io, &buf);
    const out = &w.interface;
    try out.print("hello {d}\n", .{42});
    try out.flush();
}
