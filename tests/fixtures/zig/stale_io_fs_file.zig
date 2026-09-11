// diag2 fixture — stale-API family 2 (0.15 intermediate idiom):
// `std.fs.File.stdout().writer(&buf)`. In zig 0.16 File moved to
// `std.Io.File` and `writer` takes an `Io` instance: `.writer(io, &buf)`.
// Expected: FAILS under `zig build-exe -fno-emit-bin` on zig 0.16.
const std = @import("std");
pub fn main() !void {
    var buf: [256]u8 = undefined;
    var w = std.fs.File.stdout().writer(&buf);
    const out = &w.interface;
    try out.print("hello {d}\n", .{42});
    try out.flush();
}
