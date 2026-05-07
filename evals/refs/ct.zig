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
        const a_src = (try r.takeDelimiter('\n')).?;
        const a = try arena.dupe(u8, a_src);
        const b_src = (try r.takeDelimiter('\n')).?;
        const b = try arena.dupe(u8, b_src);
        if (a.len != b.len) {
            var sink: u8 = 1;
            for (a) |ch| sink |= ch;
            std.mem.doNotOptimizeAway(sink);
            try w.writeAll("false\n");
            continue;
        }
        var diff: u8 = 0;
        var i: usize = 0;
        while (i < a.len) : (i += 1) diff |= a[i] ^ b[i];
        try w.writeAll(if (diff == 0) "true\n" else "false\n");
    }
    try w.flush();
}
