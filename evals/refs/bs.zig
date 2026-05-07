const std = @import("std");

fn erfAS(x: f64) f64 {
    const p = 0.3275911;
    const a1 = 0.254829592;
    const a2 = -0.284496736;
    const a3 = 1.421413741;
    const a4 = -1.453152027;
    const a5 = 1.061405429;
    const sign: f64 = if (x < 0.0) -1.0 else 1.0;
    const xa = @abs(x);
    const t = 1.0 / (1.0 + p * xa);
    const y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1) * t * @exp(-xa*xa);
    return sign * y;
}
fn ncdf(x: f64) f64 {
    return 0.5 * (1.0 + erfAS(x / @sqrt(@as(f64, 2.0))));
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const S = try std.fmt.parseFloat(f64, it.next().?);
        const K = try std.fmt.parseFloat(f64, it.next().?);
        const Ty = try std.fmt.parseFloat(f64, it.next().?);
        const rate = try std.fmt.parseFloat(f64, it.next().?);
        const sigma = try std.fmt.parseFloat(f64, it.next().?);
        const d1 = (@log(S / K) + (rate + 0.5 * sigma * sigma) * Ty) / (sigma * @sqrt(Ty));
        const d2 = d1 - sigma * @sqrt(Ty);
        const price = S * ncdf(d1) - K * @exp(-rate * Ty) * ncdf(d2);
        try w.print("{d:.4}\n", .{price});
    }
    try w.flush();
}
