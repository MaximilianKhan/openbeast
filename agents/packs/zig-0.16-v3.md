=== Language notes: zig 0.16 (awareness pack) ===
The installed compiler is zig 0.16.0. Idioms from zig 0.11-0.15 shown as OLD below no longer compile; write the NEW form. Check with `zig build-exe -fno-emit-bin f.zig` (or `zig test f.zig`).

(1) CURATED rename/idiom map — every line machine-verified on zig 0.16.0 on 2026-09-11 (tests/fixtures/zig016: OLD fails, NEW compiles). Not checksum-pinned.
main / Io plumbing:
- `pub fn main() !void` still works, but all I/O needs an `Io` value: prefer `pub fn main(init: std.process.Init) !void { const io = init.io; const gpa = init.gpa; ... }`. From a plain main(): `const io = std.Io.Threaded.global_single_threaded.io();`
- `std.io` is GONE (no getStdOut/getStdIn/bufferedWriter), so are `std.fs.File` and `std.fs.cwd()`. stdout: `var buf: [1024]u8 = undefined; var fw: std.Io.File.Writer = .init(.stdout(), io, &buf); const w = &fw.interface; try w.print("{d}\n", .{x}); try w.flush();` (stderr: `.stderr()`; unbuffered: `std.debug.print`).
- `std.posix.write` is GONE but `std.posix.read` is not, so posix is NOT intact: raw output is `try std.Io.File.stdout().writeStreamingAll(io, bytes);`, libc escape hatch `_ = std.c.write(1, bytes.ptr, bytes.len);` (`-lc` off macOS).
- whole working 0.16 main (args, io, stdout): read `lib/init/src/main.zig` under `zig env`'s lib_dir before grepping lib/std.
- stdin by line: `var rb: [4096]u8 = undefined; var fr: std.Io.File.Reader = .init(.stdin(), io, &rb); const r = &fr.interface; while (try r.takeDelimiter('\n')) |line| { ... }` (line excludes '\n', null at EOF). Whole input: `try r.allocRemaining(gpa, .unlimited)`. `readUntilDelimiterOrEof`/`readAll` are gone.
- files: `std.Io.Dir.cwd().readFileAlloc(io, path, gpa, .limited(1 << 20))`; `.writeFile(io, .{ .sub_path = p, .data = bytes })`; `.openFile(io, p, .{})` / `.createFile(io, p, .{})`, then `std.Io.File.Reader/Writer = .init(f, io, &buf)`; `f.close(io)`.
- args: `std.process.argsAlloc` gone → `const args = try init.minimal.args.toSlice(init.arena.allocator());`
- time/random: `std.time.nanoTimestamp/timestamp/sleep`, `std.Thread.sleep`, `std.crypto.random` gone → `const t0 = std.Io.Clock.now(.awake, io); t0.durationTo(t1).toNanoseconds()` (`.real` = wall clock), `try io.sleep(.fromMilliseconds(5), .awake)`, `io.random(&buf)` or `std.Random.DefaultPrng.init(42)`.
ArrayList (unmanaged IS std.ArrayList now):
- OLD `std.ArrayList(T).init(gpa)` / `list.append(x)` / `list.deinit()` → NEW `var list: std.ArrayList(T) = .empty; defer list.deinit(gpa); try list.append(gpa, x); try list.appendSlice(gpa, s); const owned = try list.toOwnedSlice(gpa);` (also `initCapacity(gpa, n)`, `list.items`, `clearRetainingCapacity()`, `orderedRemove(i)`). `pop()` returns `?T`. `std.ArrayListUnmanaged(T) = .{}` → `.empty`. Legacy managed form still exists: `std.array_list.Managed(T).init(gpa)`.
- `list.writer()` gone → `var aw: std.Io.Writer.Allocating = .init(gpa); try aw.writer.print("{d}", .{x}); const s = aw.written();`, or `try list.print(gpa, "{d}", .{x})` on ArrayList(u8); fixed buffer: `var w: std.Io.Writer = .fixed(&buf); ... w.buffered()`.
- PriorityQueue: OLD `.init(gpa, ctx)`/`add`/`remove` → NEW `var pq: std.PriorityQueue(T, void, cmp) = .empty; try pq.push(gpa, x); const top = pq.pop(); // ?T` plus `peek()`, `count()`, `pq.deinit(gpa)`. AutoHashMap/StringHashMap are still managed: `.init(gpa)`, `put(k, v)`, `get(k)`, `deinit()`.
removed / renamed helpers:
- `std.math.abs/absInt` → `@abs(x)`; `std.math.min/max` → `@min(a, b)` / `@max(a, b)`. Still present: `math.sqrt, pow(T, a, b), cast(T, x) ?T, maxInt(T), isPowerOfTwo, log2_int, divCeil, clamp`.
- no `@intClz`. `@clz(v)` on a u32 is a u6, but a u32 shift amount is `std.math.Log2Int(u32)` (u5): `const s: std.math.Log2Int(u32) = @intCast(@clz(v)); x << s` (v != 0). No `std.math.erf`/`erfc`: write it (A&S 7.1.26, abs error 1.5e-7).
- `std.mem.trimRight/trimLeft` → `trimEnd/trimStart(u8, s, " ")`; `mem.tokenize/split(u8, s, "x")` → `tokenizeScalar(u8, s, ' ')` / `splitScalar` (also `tokenizeAny`, `splitSequence`); `mem.copy/set` → `@memcpy(dst, src)` / `@memset(dst, 0)`; `std.sort.sort` → `std.mem.sort(T, items, {}, std.sort.asc(T))`.
- `std.fmt.format(w, ..)` → `w.print(..)`; `fmt.formatIntBuf` → `fmt.printInt(&buf, v, 10, .lower, .{})`; `fmt.fmtSliceHexLower(b)` → `"{x}"` with the slice; `bufPrint/allocPrint/parseInt/parseFloat` unchanged. Custom formatting: `pub fn format(self: T, w: *std.Io.Writer) std.Io.Writer.Error!void` printed with `{f}`; floats `{d}` / `{d:.3}`, enum tag `{t}`, anything `{any}`.
- casts take ONE arg, type from context: `@intToFloat/@floatToInt/@intCast(T, x)/@enumToInt/@boolToInt` → `const f: f64 = @floatFromInt(i); const n: i32 = @intFromFloat(f); const b: u8 = @intCast(x); @intFromEnum(e); @enumFromInt(n); @intFromBool(b); @truncate(x); @bitCast(x)`; inline: `@as(f64, @floatFromInt(n))`.
- `std.heap.GeneralPurposeAllocator` gone → `var dbg: std.heap.DebugAllocator(.{}) = .init; const gpa = dbg.allocator();` (or `init.gpa`, `std.heap.page_allocator`). `std.os.exit` → `std.process.exit(code)`.
- `std.ascii.isAlpha/isSpace` → `isAlphabetic/isWhitespace` (also `isDigit, isAlphanumeric, toLower, toUpper, eqlIgnoreCase`).
threads / Io arity / syntax / lint (each of these is a compile ERROR in 0.16):
- `std.Thread.spawn(config, f, args)`: arg 1 is a SpawnConfig (`.{}`, or `.{ .allocator = gpa }`), never an allocator; args is ALWAYS a tuple, even for one value: `const t = try std.Thread.spawn(.{}, worker, .{ ctx, i }); t.join();` (`.{ctx}`, not `ctx`). Cores: `try std.Thread.getCpuCount()`.
- `std.Thread.ResetEvent/Mutex/Condition` are gone → `var ev: std.Io.Event = .unset; ev.set(io); try ev.wait(io);` and `var m: std.Io.Mutex = .init; try m.lock(io); defer m.unlock(io);` (pass `io` into the thread's args).
- Io arity: `std.Io.File.stdin()/.stdout()` take NO args; `f.reader(io, &buf)` / `f.writer(io, &buf)` take io first. `std.Io.File` has no `writeAll` → `try f.writeStreamingAll(io, bytes)` (or print via a File.Writer). There is no `openFileIo`: `dir.openFile(io, path, .{})`.
- `const std = @import("std");` (`@std` does not exist). While continue expression needs parens: `while (i < n) : (i += 1) {`.
- lint: an unused local constant/var is an error (delete it); a `var` never mutated → `const`; `_ = x;` after x is used is a "pointless discard" (delete it); unused param → name it `_`. `i0`, `i2`, `u8`, `f32`... are primitive type names: never use them as identifiers (`r0`, `idx2`).
(2) GENERATED signature digest — zig 0.16.0 std, 11 lines, sha256(digest)=0fb5a244b8de658c (agents/packs/gen_zig_pack.py — do not edit)
legend: io = init.io (std.process.Init); w = &file_writer.interface (*std.Io.Writer); r = &file_reader.interface (*std.Io.Reader); fw/fr = std.Io.File.Writer/Reader; list: std.ArrayList(T); gpa: std.mem.Allocator; dir = std.Io.Dir.cwd(). Ranked by in-tree reference count; example args are placeholders.
- w.print(comptime fmt: []const u8, args: anytype) Error!void → try w.print("{d}\n", .{x})
- w.writeAll(bytes: []const u8) Error!void → try w.writeAll(s)
- std.Io.Reader.fixed(buffer: []const u8) Reader → std.Io.Reader.fixed(s)
- r.buffered() []u8 → r.buffered()
- file.close(io: Io) void → file.close(io)
- dir.close(io: Io) void → dir.close(io)
- list.deinit(gpa: Allocator) void → list.deinit(gpa)
- list.append(gpa: Allocator, item: T) Allocator.Error!void → try list.append(gpa, item)
- std.ascii.eqlIgnoreCase(a: []const u8, b: []const u8) bool → std.ascii.eqlIgnoreCase(s, s)
- std.fmt.allocPrint(gpa: Allocator, comptime fmt: []const u8, args: anytype) Allocator.Error![]u8 → try std.fmt.allocPrint(gpa, "{d}\n", .{x})
- std.mem.eql(comptime T: type, a: []const T, b: []const T) bool → std.mem.eql(u8, a, b)
