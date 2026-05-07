const std = @import("std");

pub fn main(init: std.process.Init) !void {
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    while (try r.takeDelimiter('\n')) |line| {
        var count: usize = 0;
        for (line) |c| {
            const lc = std.ascii.toLower(c);
            if (lc == 'a' or lc == 'e' or lc == 'i' or lc == 'o' or lc == 'u') {
                count += 1;
            }
        }
        try w.print("{d}\n", .{count});
    }
    try w.flush();
}
