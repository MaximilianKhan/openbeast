const std = @import("std");

const Point = struct { x: i64, y: i64 };

fn cross(o: Point, a: Point, b: Point) i64 {
    return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

fn ptLess(_: void, a: Point, b: Point) bool {
    if (a.x != b.x) return a.x < b.x;
    return a.y < b.y;
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");

    var out_buf: [8192]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var case: usize = 0;
    while (case < t) : (case += 1) {
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const pts = try arena.alloc(Point, n);
        var i: usize = 0;
        while (i < n) : (i += 1) {
            pts[i].x = try std.fmt.parseInt(i64, it.next().?, 10);
            pts[i].y = try std.fmt.parseInt(i64, it.next().?, 10);
        }
        std.sort.heap(Point, pts, {}, ptLess);

        const hull = try arena.alloc(Point, 2 * n + 1);
        var k: usize = 0;
        // Lower hull
        i = 0;
        while (i < n) : (i += 1) {
            while (k >= 2 and cross(hull[k - 2], hull[k - 1], pts[i]) <= 0) k -= 1;
            hull[k] = pts[i];
            k += 1;
        }
        // Upper hull
        const lower_end = k + 1;
        if (n >= 2) {
            var j: usize = n - 1;
            while (j > 0) {
                j -= 1;
                while (k >= lower_end and cross(hull[k - 2], hull[k - 1], pts[j]) <= 0) k -= 1;
                hull[k] = pts[j];
                k += 1;
            }
        }
        const hsize = k - 1;
        try w.print("{d}\n", .{hsize});
        var h: usize = 0;
        while (h < hsize) : (h += 1) {
            try w.print("{d} {d}\n", .{ hull[h].x, hull[h].y });
        }
    }
    try w.flush();
}
