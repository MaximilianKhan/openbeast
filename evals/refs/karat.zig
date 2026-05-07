const std = @import("std");

// Schoolbook multiplication on base-256 little-endian byte arrays.
// (Karatsuba would be more impressive but for the audit reference impl,
// correctness is the only requirement; inputs are <= 80 bytes.)
fn multiply(allocator: std.mem.Allocator, a: []const u8, b: []const u8) ![]u8 {
    const n = a.len + b.len;
    const result = try allocator.alloc(u32, n);
    @memset(result, 0);
    for (a, 0..) |ai, i| {
        if (ai == 0) continue;
        for (b, 0..) |bj, j| {
            result[i + j] += @as(u32, ai) * @as(u32, bj);
        }
    }
    // Carry propagate
    var carry: u32 = 0;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const v = result[i] + carry;
        result[i] = v & 0xff;
        carry = v >> 8;
    }
    // Trim leading zeros (keep at least 1)
    var len = n;
    while (len > 1 and result[len - 1] == 0) len -= 1;
    const bytes = try allocator.alloc(u8, len);
    var k: usize = 0;
    while (k < len) : (k += 1) bytes[k] = @intCast(result[k]);
    return bytes;
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();

    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var data: std.ArrayList(u8) = .empty;
    try r.appendRemainingUnlimited(arena, &data);
    var it = std.mem.tokenizeAny(u8, data.items, " \n\r\t");

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t = try std.fmt.parseInt(usize, it.next().?, 10);
    var case: usize = 0;
    while (case < t) : (case += 1) {
        const na = try std.fmt.parseInt(usize, it.next().?, 10);
        const nb = try std.fmt.parseInt(usize, it.next().?, 10);
        const a = try arena.alloc(u8, na);
        const b = try arena.alloc(u8, nb);
        var i: usize = 0;
        while (i < na) : (i += 1) a[i] = try std.fmt.parseInt(u8, it.next().?, 10);
        i = 0;
        while (i < nb) : (i += 1) b[i] = try std.fmt.parseInt(u8, it.next().?, 10);
        const product = try multiply(arena, a, b);
        var j: usize = 0;
        while (j < product.len) : (j += 1) {
            if (j > 0) try w.writeByte(' ');
            try w.print("{d}", .{product[j]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
