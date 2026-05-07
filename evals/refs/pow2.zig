const std = @import("std");

pub fn main(init: std.process.Init) !void {
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    while (try r.takeDelimiter('\n')) |line| {
        const n = std.fmt.parseInt(i64, line, 10) catch {
            try w.writeAll("false\n");
            continue;
        };
        const is_pow = n > 0 and (n & (n - 1)) == 0;
        try w.writeAll(if (is_pow) "true\n" else "false\n");
    }
    try w.flush();
}
