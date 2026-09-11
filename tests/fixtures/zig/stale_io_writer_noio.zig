// diag2 fixture — stale-API family 2 (arity form): right module, wrong
// arity — `std.Io.File.stdout().writer(&buf)` without the `Io` argument.
// zig 0.16: `File.writer(file, io, buffer)`. The `note: function declared
// here` line carries the signature — diag2 must KEEP it.
// Expected: FAILS under `zig build-exe -fno-emit-bin` on zig 0.16.
const std = @import("std");
pub fn main() !void {
    var buf: [256]u8 = undefined;
    var w = std.Io.File.stdout().writer(&buf);
    const out = &w.interface;
    try out.print("hello {d}\n", .{42});
    try out.flush();
}
