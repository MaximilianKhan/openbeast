const std = @import("std");
const builtin = @import("builtin");
pub fn main() void {
    const bytes = "hello\n";
    // The libc escape hatch. macOS always links libc; elsewhere `std.c.*`
    // needs `-lc` ("dependency on libc must be explicitly specified").
    if (comptime builtin.os.tag.isDarwin() or builtin.link_libc) {
        _ = std.c.write(1, bytes.ptr, bytes.len);
    }
}
