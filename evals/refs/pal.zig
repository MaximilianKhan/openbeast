const std = @import("std");

pub fn main(init: std.process.Init) !void {
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    var clean: [4096]u8 = undefined;

    while (try r.takeDelimiter('\n')) |line| {
        var n: usize = 0;
        for (line) |c| {
            if (std.ascii.isAlphanumeric(c)) {
                clean[n] = std.ascii.toLower(c);
                n += 1;
            }
        }
        var is_pal = true;
        var i: usize = 0;
        while (i < n / 2) : (i += 1) {
            if (clean[i] != clean[n - 1 - i]) {
                is_pal = false;
                break;
            }
        }
        try w.writeAll(if (is_pal) "true\n" else "false\n");
    }
    try w.flush();
}
