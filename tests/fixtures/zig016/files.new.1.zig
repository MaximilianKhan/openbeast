const std = @import("std");
pub fn main(init: std.process.Init) !void {
    const io = init.io;
    const gpa = init.gpa;
    const data = try std.Io.Dir.cwd().readFileAlloc(io, "x.txt", gpa, .limited(1 << 20));
    defer gpa.free(data);
    try std.Io.Dir.cwd().writeFile(io, .{ .sub_path = "out.txt", .data = data });
    const f = try std.Io.Dir.cwd().createFile(io, "out2.txt", .{});
    defer f.close(io);
    var buf: [1024]u8 = undefined;
    var fw: std.Io.File.Writer = .init(f, io, &buf);
    try fw.interface.print("{d}\n", .{1});
    try fw.interface.flush();
    const rf = try std.Io.Dir.cwd().openFile(io, "x.txt", .{});
    defer rf.close(io);
    var rbuf: [1024]u8 = undefined;
    var fr: std.Io.File.Reader = .init(rf, io, &rbuf);
    while (try fr.interface.takeDelimiter('\n')) |line| std.debug.print("{s}\n", .{line});
}
