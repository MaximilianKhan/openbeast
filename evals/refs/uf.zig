const std = @import("std");

const UF = struct {
    parent: []usize,
    size: []usize,

    fn find(self: *UF, x: usize) usize {
        var cur = x;
        while (self.parent[cur] != cur) cur = self.parent[cur];
        var i = x;
        while (self.parent[i] != cur) {
            const nxt = self.parent[i];
            self.parent[i] = cur;
            i = nxt;
        }
        return cur;
    }

    fn unite(self: *UF, a: usize, b: usize) void {
        const ra = self.find(a);
        const rb = self.find(b);
        if (ra == rb) return;
        if (self.size[ra] < self.size[rb]) {
            self.parent[ra] = rb;
            self.size[rb] += self.size[ra];
        } else {
            self.parent[rb] = ra;
            self.size[ra] += self.size[rb];
        }
    }
};

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
    const n = try std.fmt.parseInt(usize, it.next().?, 10);
    const q = try std.fmt.parseInt(usize, it.next().?, 10);

    var uf: UF = .{
        .parent = try arena.alloc(usize, n),
        .size = try arena.alloc(usize, n),
    };
    var i: usize = 0;
    while (i < n) : (i += 1) {
        uf.parent[i] = i;
        uf.size[i] = 1;
    }

    var k: usize = 0;
    while (k < q) : (k += 1) {
        const op = it.next().?;
        const a = try std.fmt.parseInt(usize, it.next().?, 10);
        const b = try std.fmt.parseInt(usize, it.next().?, 10);
        if (op[0] == 'u') {
            uf.unite(a, b);
        } else {
            const same = uf.find(a) == uf.find(b);
            try w.writeAll(if (same) "true\n" else "false\n");
        }
    }
    try w.flush();
}
