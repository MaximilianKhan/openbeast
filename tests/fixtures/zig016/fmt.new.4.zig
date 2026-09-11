const std = @import("std");
const P = struct {
    x: i32,
    pub fn format(self: P, w: *std.Io.Writer) std.Io.Writer.Error!void {
        try w.print("P({d})", .{self.x});
    }
};
pub fn main() !void {
    std.debug.print("{f}\n", .{P{ .x = 1 }});
}
