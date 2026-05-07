const std = @import("std");

fn run(allocator: std.mem.Allocator, code: []const u8, inp: []const u8, w: *std.Io.Writer) !void {
    const n = code.len;
    const pairs = try allocator.alloc(usize, n);
    @memset(pairs, 0);
    var stack: std.ArrayList(usize) = .empty;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        if (code[i] == '[') try stack.append(allocator, i)
        else if (code[i] == ']') {
            const j = stack.items[stack.items.len - 1];
            stack.items.len -= 1;
            pairs[i] = j; pairs[j] = i;
        }
    }
    const tape = try allocator.alloc(u8, 30000);
    @memset(tape, 0);
    var ptr: usize = 0; var ip: usize = 0; var ipos: usize = 0;
    while (ip < n) {
        const c = code[ip];
        switch (c) {
            '>' => ptr += 1,
            '<' => ptr -= 1,
            '+' => tape[ptr] = tape[ptr] +% 1,
            '-' => tape[ptr] = tape[ptr] -% 1,
            '.' => try w.writeByte(tape[ptr]),
            ',' => {
                tape[ptr] = if (ipos < inp.len) inp[ipos] else 0;
                ipos += 1;
            },
            '[' => { if (tape[ptr] == 0) ip = pairs[ip]; },
            ']' => { if (tape[ptr] != 0) ip = pairs[ip]; },
            else => {},
        }
        ip += 1;
    }
}

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    var in_buf: [65536]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;
    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    const t_line = (try r.takeDelimiter('\n')).?;
    const t = try std.fmt.parseInt(usize, t_line, 10);
    var c: usize = 0;
    while (c < t) : (c += 1) {
        const code_src = (try r.takeDelimiter('\n')).?;
        const code = try arena.dupe(u8, code_src);
        const inp_src = (try r.takeDelimiter('\n')).?;
        const inp = try arena.dupe(u8, inp_src);
        try run(arena, code, inp, w);
        try w.writeByte('\n');
    }
    try w.flush();
}
