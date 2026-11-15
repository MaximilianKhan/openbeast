// HANG PROBE: never terminates.
const std = @import("std");

pub fn main() void {
    var i: u64 = 0;
    while (true) {
        i +%= 1;
        std.mem.doNotOptimizeAway(i);
    }
}
