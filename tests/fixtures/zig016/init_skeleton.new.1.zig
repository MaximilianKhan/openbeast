// The shape of <zig env .lib_dir>/init/src/main.zig (the toolchain's own
// `zig init` template), minus its build-system import: compiles on 0.16.0.
const std = @import("std");
const Io = std.Io;
pub fn main(init: std.process.Init) !void {
    const arena: std.mem.Allocator = init.arena.allocator();
    const args = try init.minimal.args.toSlice(arena);
    const io = init.io;
    var stdout_buffer: [1024]u8 = undefined;
    var stdout_file_writer: Io.File.Writer = .init(.stdout(), io, &stdout_buffer);
    const stdout_writer = &stdout_file_writer.interface;
    try stdout_writer.print("{d} args\n", .{args.len});
    try stdout_writer.flush();
}
