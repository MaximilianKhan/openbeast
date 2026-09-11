// diag2 fixture — stale-API family 2: std.Io writer/reader contract.
// zig ≤0.14 idiom `std.io.getStdOut().writer()`. In zig 0.16 the std.Io
// rework moved stdout to `std.fs.File.stdout()` and writers are
// `std.Io.Writer` interfaces obtained via a buffered `.interface`.
// Expected: FAILS under `zig build-exe -fno-emit-bin` on zig 0.16.
const std = @import("std");
pub fn main() !void {
    const stdout = std.io.getStdOut().writer();
    try stdout.print("hello {d}\n", .{42});
}
