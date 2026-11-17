const std = @import("std");
pub fn main() !void {
    const out = "hello\n";
    var off: usize = 0;
    while (off < out.len) off += try std.posix.write(1, out[off..]);
}
