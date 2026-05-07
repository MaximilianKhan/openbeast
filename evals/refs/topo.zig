const std = @import("std");

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);

    var out_buf: [8192]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const t = try std.fmt.parseInt(usize, it.next().?, 10);

    var case: usize = 0;
    while (case < t) : (case += 1) {
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const m = try std.fmt.parseInt(usize, it.next().?, 10);
        const adj = try arena.alloc(std.ArrayList(usize), n);
        for (adj) |*a| a.* = .empty;
        const indeg = try arena.alloc(usize, n);
        for (indeg) |*x| x.* = 0;

        var k: usize = 0;
        while (k < m) : (k += 1) {
            const u = try std.fmt.parseInt(usize, it.next().?, 10);
            const v = try std.fmt.parseInt(usize, it.next().?, 10);
            try adj[u].append(arena, v);
            indeg[v] += 1;
        }

        var queue: std.ArrayList(usize) = .empty;
        var qhead: usize = 0;
        var i: usize = 0;
        while (i < n) : (i += 1) {
            if (indeg[i] == 0) try queue.append(arena, i);
        }

        var order: std.ArrayList(usize) = .empty;
        while (qhead < queue.items.len) {
            const u = queue.items[qhead];
            qhead += 1;
            try order.append(arena, u);
            for (adj[u].items) |v| {
                indeg[v] -= 1;
                if (indeg[v] == 0) try queue.append(arena, v);
            }
        }

        if (order.items.len != n) {
            try w.writeAll("CYCLE\n");
        } else {
            var j: usize = 0;
            while (j < order.items.len) : (j += 1) {
                if (j > 0) try w.writeByte(' ');
                try w.print("{d}", .{order.items[j]});
            }
            try w.writeByte('\n');
        }
    }
    try w.flush();
}
