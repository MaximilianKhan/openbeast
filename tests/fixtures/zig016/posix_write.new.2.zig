const std = @import("std");
pub fn main() void {
    const bytes = "hello\n";
    _ = std.c.write(1, bytes.ptr, bytes.len);
}
