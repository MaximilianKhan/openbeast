const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t_line = (try r.takeDelimiter('\n')).?;
    const t = try std.fmt.parseInt(usize, t_line, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const n_line = (try r.takeDelimiter('\n')).?;
        const n = try std.fmt.parseInt(usize, n_line, 10);
        const v_line_src = (try r.takeDelimiter('\n')).?;
        // copy out of internal buffer since takeDelimiter aliases it
        const v_line = try arena.dupe(u8, v_line_src);
        if (n == 0) {
            try w.writeByte('\n');
            continue;
        }
        var toks: std.ArrayList([]const u8) = .empty;
        var sit = std.mem.tokenizeAny(u8, v_line, " \t\r");
        while (sit.next()) |tok| try toks.append(arena, tok);
        var k: i64 = @as(i64, @intCast(toks.items.len)) - 1;
        var first = true;
        while (k >= 0) : (k -= 1) {
            if (!first) try w.writeByte(' ');
            first = false;
            try w.writeAll(toks.items[@intCast(k)]);
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
