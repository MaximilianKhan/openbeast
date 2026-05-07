const std = @import("std");

const Node = struct {
    v: i64,
    l: ?*Node = null,
    r: ?*Node = null,
};

fn insert(allocator: std.mem.Allocator, root: ?*Node, v: i64) !*Node {
    if (root == null) {
        const n = try allocator.create(Node);
        n.* = .{ .v = v };
        return n;
    }
    var cur = root.?;
    while (true) {
        if (v < cur.v) {
            if (cur.l == null) {
                const n = try allocator.create(Node);
                n.* = .{ .v = v };
                cur.l = n;
                return root.?;
            }
            cur = cur.l.?;
        } else if (v > cur.v) {
            if (cur.r == null) {
                const n = try allocator.create(Node);
                n.* = .{ .v = v };
                cur.r = n;
                return root.?;
            }
            cur = cur.r.?;
        } else return root.?;
    }
}

fn inorder(root: ?*Node, out: *std.ArrayList(i64), allocator: std.mem.Allocator) !void {
    var stack: std.ArrayList(*Node) = .empty;
    defer stack.deinit(allocator);
    var cur = root;
    while (cur != null or stack.items.len > 0) {
        while (cur) |c| {
            try stack.append(allocator, c);
            cur = c.l;
        }
        const c = stack.items[stack.items.len - 1];
        stack.items.len -= 1;
        try out.append(allocator, c.v);
        cur = c.r;
    }
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var out_buf: [16384]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");
    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        var root: ?*Node = null;
        var i: usize = 0;
        while (i < n) : (i += 1) {
            const v = try std.fmt.parseInt(i64, it.next().?, 10);
            root = try insert(arena, root, v);
        }
        var vs: std.ArrayList(i64) = .empty;
        try inorder(root, &vs, arena);
        var k: usize = 0;
        while (k < vs.items.len) : (k += 1) {
            if (k > 0) try w.writeByte(' ');
            try w.print("{d}", .{vs.items[k]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
