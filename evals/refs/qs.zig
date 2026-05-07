const std = @import("std");

fn threeWayQS(a: []i64, lo: usize, hi: usize) void {
    if (lo >= hi) return;
    const pivot = a[lo + (hi - lo) / 2];
    var lt: usize = lo;
    var gt: usize = hi;
    var i: usize = lo;
    while (i <= gt) {
        if (a[i] < pivot) {
            const tmp = a[lt];
            a[lt] = a[i];
            a[i] = tmp;
            lt += 1;
            i += 1;
        } else if (a[i] > pivot) {
            const tmp = a[gt];
            a[gt] = a[i];
            a[i] = tmp;
            if (gt == 0) break;
            gt -= 1;
        } else {
            i += 1;
        }
    }
    if (lt > 0) threeWayQS(a, lo, lt - 1);
    threeWayQS(a, gt + 1, hi);
}

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
    const t = try std.fmt.parseInt(usize, it.next().?, 10);

    var case: usize = 0;
    while (case < t) : (case += 1) {
        const n = try std.fmt.parseInt(usize, it.next().?, 10);
        const arr = try arena.alloc(i64, n);
        var k: usize = 0;
        while (k < n) : (k += 1) {
            arr[k] = try std.fmt.parseInt(i64, it.next().?, 10);
        }
        if (n > 0) threeWayQS(arr, 0, n - 1);
        var j: usize = 0;
        while (j < n) : (j += 1) {
            if (j > 0) try w.writeByte(' ');
            try w.print("{d}", .{arr[j]});
        }
        try w.writeByte('\n');
    }
    try w.flush();
}
