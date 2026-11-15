// SLOW (163_f): never finishes.
const std = @import("std");
pub fn main() void {
    var x: u64 = 0;
    while (true) {
        x +%= 1;
        std.mem.doNotOptimizeAway(x);
    }
}
