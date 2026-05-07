const std = @import("std");

const Heap = struct {
    h: std.ArrayList(i64),
    fn push(self: *Heap, allocator: std.mem.Allocator, v: i64) !void {
        try self.h.append(allocator, v);
        var i: usize = self.h.items.len - 1;
        while (i > 0) {
            const p = (i - 1) / 2;
            if (self.h.items[p] > self.h.items[i]) {
                const t = self.h.items[p];
                self.h.items[p] = self.h.items[i];
                self.h.items[i] = t;
                i = p;
            } else break;
        }
    }
    fn pop(self: *Heap) ?i64 {
        if (self.h.items.len == 0) return null;
        const top = self.h.items[0];
        const last = self.h.items[self.h.items.len - 1];
        self.h.items.len -= 1;
        if (self.h.items.len > 0) {
            self.h.items[0] = last;
            var i: usize = 0;
            const n = self.h.items.len;
            while (true) {
                const l = 2 * i + 1;
                const r = 2 * i + 2;
                var m = i;
                if (l < n and self.h.items[l] < self.h.items[m]) m = l;
                if (r < n and self.h.items[r] < self.h.items[m]) m = r;
                if (m != i) {
                    const t = self.h.items[m];
                    self.h.items[m] = self.h.items[i];
                    self.h.items[i] = t;
                    i = m;
                } else break;
            }
        }
        return top;
    }
};

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var out_buf: [16384]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t_line = (try r.takeDelimiter('\n')).?;
    const t = try std.fmt.parseInt(usize, t_line, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const q_line = (try r.takeDelimiter('\n')).?;
        const q = try std.fmt.parseInt(usize, q_line, 10);
        var heap: Heap = .{ .h = .empty };
        var i: usize = 0;
        while (i < q) : (i += 1) {
            const line_src = (try r.takeDelimiter('\n')).?;
            const line = try arena.dupe(u8, line_src);
            if (line[0] == 'p') {
                const v = try std.fmt.parseInt(i64, line[2..], 10);
                try heap.push(arena, v);
            } else {
                if (heap.pop()) |v| {
                    try w.print("{d}\n", .{v});
                } else {
                    try w.writeAll("-\n");
                }
            }
        }
    }
    try w.flush();
}
