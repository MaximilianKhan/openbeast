const std = @import("std");

fn accel(p: [][2]f64, mass: []const f64, n: usize, allocator: std.mem.Allocator) ![][2]f64 {
    const a = try allocator.alloc([2]f64, n);
    for (a) |*x| x.* = .{ 0, 0 };
    var i: usize = 0;
    while (i < n) : (i += 1) {
        var j: usize = 0;
        while (j < n) : (j += 1) {
            if (i == j) continue;
            const dx = p[j][0] - p[i][0];
            const dy = p[j][1] - p[i][1];
            const r2 = dx * dx + dy * dy;
            const r = @sqrt(r2);
            if (r > 1e-12) {
                const f = mass[j] / (r2 * r);
                a[i][0] += f * dx;
                a[i][1] += f * dy;
            }
        }
    }
    return a;
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
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const dt = try std.fmt.parseFloat(f64, it.next().?);
        const steps = try std.fmt.parseInt(usize, it.next().?, 10);
        const mass = try arena.alloc(f64, n);
        const pos = try arena.alloc([2]f64, n);
        const vel = try arena.alloc([2]f64, n);
        var i: usize = 0;
        while (i < n) : (i += 1) {
            mass[i] = try std.fmt.parseFloat(f64, it.next().?);
            pos[i][0] = try std.fmt.parseFloat(f64, it.next().?);
            pos[i][1] = try std.fmt.parseFloat(f64, it.next().?);
            vel[i][0] = try std.fmt.parseFloat(f64, it.next().?);
            vel[i][1] = try std.fmt.parseFloat(f64, it.next().?);
        }
        var a = try accel(pos, mass, n, arena);
        var s: usize = 0;
        while (s < steps) : (s += 1) {
            i = 0;
            while (i < n) : (i += 1) {
                pos[i][0] += vel[i][0] * dt + 0.5 * a[i][0] * dt * dt;
                pos[i][1] += vel[i][1] * dt + 0.5 * a[i][1] * dt * dt;
            }
            const a2 = try accel(pos, mass, n, arena);
            i = 0;
            while (i < n) : (i += 1) {
                vel[i][0] += 0.5 * (a[i][0] + a2[i][0]) * dt;
                vel[i][1] += 0.5 * (a[i][1] + a2[i][1]) * dt;
            }
            a = a2;
        }
        i = 0;
        while (i < n) : (i += 1) {
            try w.print("{d:.6} {d:.6} {d:.6} {d:.6}\n", .{ pos[i][0], pos[i][1], vel[i][0], vel[i][1] });
        }
    }
    try w.flush();
}
