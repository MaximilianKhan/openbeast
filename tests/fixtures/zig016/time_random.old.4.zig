const std = @import("std");
pub fn main() !void {
    var prng = std.Random.DefaultPrng.init(@intCast(std.time.timestamp()));
    _ = prng.random().int(u8);
}
