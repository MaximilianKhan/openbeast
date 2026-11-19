# Zig 0.16 reference-authoring recipe (sealed)

Moved here from `skills/eval-variant-porter/SKILL.md` on 2026-11-19 (docs review).
It is the stdio API recipe eval AUTHORS need to write Zig reference solutions.
v5 bans exactly this wording in Zig task prompts as tutoring, and `skills/` is
readable by the measured agent, so it lives under `evals/audit/`, which the eval
sandbox seals (`agents/tools.py`). Measured runs get Zig help only through the
measured, hash-pinned language packs.

## Zig 0.16 canonical patterns

> **Why this section exists.** The original Zig variant rollout taught the
> model to call methods like `takeDelimiter` and `appendRemainingUnlimited`
> directly on `std.Io.File.Reader`. Those methods don't live there — they
> live on the underlying `std.Io.Reader` *interface*, accessed via
> `&fr.interface`. The omission scored 0/13 across both 27B models in the
> first v3 sweep. These templates are the verified fix **for reference
> solutions**. Since v5 they must NOT appear in a variant's `task` field
> (see the ban above): a prompt that teaches the API measures the prompt.

### Critical API rules

1. **Capital `Io`.** `std.Io`, not `std.io`. The lowercase namespace was
   removed in Zig 0.15+.
2. **The `.interface` indirection.** `std.Io.File.Reader` and
   `std.Io.File.Writer` are concrete file readers/writers with ~10 public
   methods. The rich API (65+ methods including `takeDelimiter`,
   `appendRemainingUnlimited`, `print`) lives on `std.Io.Reader` /
   `std.Io.Writer` (the interface). Get there via `const r = &fr.interface;`.
3. **`init.io` and `init.arena`.** The `Init` param exposes both. Pass
   `init.io` to `.reader()` / `.writer()`. Use `init.arena.allocator()` for
   transient heap allocations.
4. **Always `try w.flush()`** before return — output is buffered and
   silently dropped on exit if you don't.

### Strict-default footguns (these are *errors*, not warnings)

- **Unused function parameter.** Rename to `_`: `pub fn main(_: std.process.Init) !void { ... }` if you don't need `init`. (You'll need it for stdio though.)
- **Unused local constant.** Discard with `_ = unused_value;` or just don't bind it.
- **`var` never mutated.** Use `const` instead. Zig errors if a `var` is never reassigned.
- **Unused imports** of named items. Prefer `const std = @import("std");` and access fields qualified — `std.foo` doesn't trigger unused-import.

### Template 1 — Line-by-line stdin → stdout (most common)

```zig
const std = @import("std");

pub fn main(init: std.process.Init) !void {
    var in_buf: [4096]u8 = undefined;
    var fr = std.Io.File.stdin().reader(init.io, &in_buf);
    const r = &fr.interface;

    var out_buf: [4096]u8 = undefined;
    var fw = std.Io.File.stdout().writer(init.io, &out_buf);
    const w = &fw.interface;

    while (try r.takeDelimiter('\n')) |line| {
        // Process `line` (a []u8 slice without the newline)
        try w.print("{s}\n", .{line});
    }
    try w.flush();
}
```

### Template 2 — Slurp all stdin → tokenize whitespace

```zig
const std = @import("std");

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
    while (it.next()) |tok| {
        const n = try std.fmt.parseInt(i64, tok, 10);
        try w.print("{d}\n", .{n});
    }
    try w.flush();
}
```

Both templates verified to compile and run on `zig 0.16.0` (mise install).
Use Template 1 when input is line-oriented (one record per line); use
Template 2 when input is a stream of whitespace-separated tokens.

### Useful interface methods (on `std.Io.Reader` via `&fr.interface`)

- `takeDelimiter(byte) !?[]u8` — read up to and excluding delimiter; returns `null` at EOF
- `takeDelimiterExclusive(byte) ![]u8` — same but errors at EOF instead of returning null
- `appendRemainingUnlimited(allocator, *ArrayList(u8)) !void` — slurp all remaining bytes
- `takeByte() !u8` — single byte
- `readSliceAll([]u8) !void` — fill a fixed-size buffer

### Useful interface methods (on `std.Io.Writer` via `&fw.interface`)

- `print(fmt, args) !void` — formatted output (`{d}`, `{s}`, `{x}`, etc.)
- `writeAll([]const u8) !void` — write a slice
- `writeByte(u8) !void` — single byte
- `flush() !void` — **must call before return**
