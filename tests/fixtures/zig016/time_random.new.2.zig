const std = @import("std");
pub fn main(init: std.process.Init) !void {
    var seed: [8]u8 = undefined;
    init.io.random(&seed);
    var prng = std.Random.DefaultPrng.init(std.mem.readInt(u64, &seed, .little));
    const r = prng.random();
    const f = r.float(f64);
    const n = r.intRangeLessThan(u32, 0, 10);
    var fixed = std.Random.DefaultPrng.init(42);
    std.debug.print("{d} {d} {d}\n", .{ f, n, fixed.random().int(u8) });
}
