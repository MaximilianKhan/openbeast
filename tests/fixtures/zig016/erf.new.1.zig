const std = @import("std");
// std.math has no erf/erfc: Abramowitz & Stegun 7.1.26, |error| < 1.5e-7.
fn erf(x: f64) f64 {
    const t = 1.0 / (1.0 + 0.3275911 * @abs(x));
    const p = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
    const y = 1.0 - p * @exp(-x * x);
    return if (x < 0) -y else y;
}
pub fn main() void {
    std.debug.print("{d:.6}\n", .{erf(0.5)});
}
