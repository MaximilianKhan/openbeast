const std = @import("std");

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t_line_src = (try r.takeDelimiter('\n')).?;
    const t = try std.fmt.parseInt(usize, t_line_src, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const hw_src = (try r.takeDelimiter('\n')).?;
        const hw = try arena.dupe(u8, hw_src);
        var hw_it = std.mem.tokenizeAny(u8, hw, " \t");
        const h = try std.fmt.parseInt(usize, hw_it.next().?, 10);
        const wd = try std.fmt.parseInt(usize, hw_it.next().?, 10);
        const grid = try arena.alloc([]u8, h);
        var i: usize = 0;
        while (i < h) : (i += 1) {
            const src = (try r.takeDelimiter('\n')).?;
            grid[i] = try arena.dupe(u8, src);
        }
        var sr: isize = -1; var sc: isize = -1;
        var gr: isize = -1; var gc: isize = -1;
        var ri: usize = 0;
        while (ri < h) : (ri += 1) {
            var ci: usize = 0;
            while (ci < wd) : (ci += 1) {
                if (grid[ri][ci] == 'S') { sr = @intCast(ri); sc = @intCast(ci); }
                else if (grid[ri][ci] == 'G') { gr = @intCast(ri); gc = @intCast(ci); }
            }
        }
        if (sr < 0 or gr < 0) { try w.writeAll("-1\n"); continue; }
        const seen = try arena.alloc(bool, h * wd);
        @memset(seen, false);
        seen[@as(usize, @intCast(sr)) * wd + @as(usize, @intCast(sc))] = true;
        const QItem = struct { r: isize, c: isize, d: i32 };
        var queue: std.ArrayList(QItem) = .empty;
        try queue.append(arena, .{ .r = sr, .c = sc, .d = 0 });
        var qhead: usize = 0;
        var found: i32 = -1;
        const dr = [_]isize{ -1, 1, 0, 0 };
        const dc = [_]isize{ 0, 0, -1, 1 };
        while (qhead < queue.items.len) {
            const item = queue.items[qhead]; qhead += 1;
            if (item.r == gr and item.c == gc) { found = item.d; break; }
            var k: usize = 0;
            while (k < 4) : (k += 1) {
                const nr = item.r + dr[k];
                const nc = item.c + dc[k];
                if (nr >= 0 and nr < @as(isize, @intCast(h)) and nc >= 0 and nc < @as(isize, @intCast(wd))) {
                    const ur: usize = @intCast(nr);
                    const uc: usize = @intCast(nc);
                    if (!seen[ur * wd + uc] and grid[ur][uc] != '#') {
                        seen[ur * wd + uc] = true;
                        try queue.append(arena, .{ .r = nr, .c = nc, .d = item.d + 1 });
                    }
                }
            }
        }
        try w.print("{d}\n", .{found});
    }
    try w.flush();
}
