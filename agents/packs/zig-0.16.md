=== Language notes: zig 0.16 (awareness pack) ===
The installed compiler is zig 0.16.0. Idioms from zig 0.11-0.15 shown as OLD below no longer compile; write the NEW form. Check with `zig build-exe -fno-emit-bin f.zig` (or `zig test f.zig`).

(1) CURATED rename/idiom map — every line machine-verified on zig 0.16.0 on 2026-09-11 (tests/fixtures/zig016: OLD fails, NEW compiles). Not checksum-pinned.
main / Io plumbing:
- `pub fn main() !void` still works, but all I/O needs an `Io` value: prefer `pub fn main(init: std.process.Init) !void { const io = init.io; const gpa = init.gpa; ... }`. From a plain main(): `const io = std.Io.Threaded.global_single_threaded.io();`
- `std.io` is GONE (no getStdOut/getStdIn/bufferedWriter), so are `std.fs.File` and `std.fs.cwd()`. stdout: `var buf: [1024]u8 = undefined; var fw: std.Io.File.Writer = .init(.stdout(), io, &buf); const w = &fw.interface; try w.print("{d}\n", .{x}); try w.flush();` (stderr: `.stderr()`; unbuffered: `std.debug.print`).
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
- `std.mem.trimRight/trimLeft` → `trimEnd/trimStart(u8, s, " ")`; `mem.tokenize/split(u8, s, "x")` → `tokenizeScalar(u8, s, ' ')` / `splitScalar` (also `tokenizeAny`, `splitSequence`); `mem.copy/set` → `@memcpy(dst, src)` / `@memset(dst, 0)`; `std.sort.sort` → `std.mem.sort(T, items, {}, std.sort.asc(T))`.
- `std.fmt.format(w, ..)` → `w.print(..)`; `fmt.formatIntBuf` → `fmt.printInt(&buf, v, 10, .lower, .{})`; `fmt.fmtSliceHexLower(b)` → `"{x}"` with the slice; `bufPrint/allocPrint/parseInt/parseFloat` unchanged. Custom formatting: `pub fn format(self: T, w: *std.Io.Writer) std.Io.Writer.Error!void` printed with `{f}`; floats `{d}` / `{d:.3}`, enum tag `{t}`, anything `{any}`.
- casts take ONE arg, type from context: `@intToFloat/@floatToInt/@intCast(T, x)/@enumToInt/@boolToInt` → `const f: f64 = @floatFromInt(i); const n: i32 = @intFromFloat(f); const b: u8 = @intCast(x); @intFromEnum(e); @enumFromInt(n); @intFromBool(b); @truncate(x); @bitCast(x)`; inline: `@as(f64, @floatFromInt(n))`.
- `std.heap.GeneralPurposeAllocator` gone → `var dbg: std.heap.DebugAllocator(.{}) = .init; const gpa = dbg.allocator();` (or `init.gpa`, `std.heap.page_allocator`). `std.os.exit` → `std.process.exit(code)`.
- `std.ascii.isAlpha/isSpace` → `isAlphabetic/isWhitespace` (also `isDigit, isAlphanumeric, toLower, toUpper, eqlIgnoreCase`).
(2) GENERATED signature digest — zig 0.16.0 std, 35 lines, sha256(digest)=c7af73f8d528b4ee (agents/packs/gen_zig_pack.py — do not edit)
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
- std.mem.readInt(comptime T: type, buffer: *const [@divExact(@typeInfo(T).int.bits, 8)]u8, endian: Endian) T → std.mem.readInt(u8, buffer, .little)
- std.math.maxInt(comptime T: type) comptime_int → std.math.maxInt(u8)
- w.writeInt(comptime T: type, value: T, endian: std.builtin.Endian) Error!void → try w.writeInt(u8, value, .little)
- w.writeByte(byte: u8) Error!void → try w.writeByte('a')
- r.takeInt(comptime T: type, endian: std.builtin.Endian) Error!T → try r.takeInt(u8, .little)
- r.takeByte() Error!u8 → try r.takeByte()
- file.reader(io: Io, buffer: []u8) Reader → file.reader(io, &buf)
- std.Io.Dir.cwd() Dir → std.Io.Dir.cwd()
- list.appendAssumeCapacity(item: T) void → list.appendAssumeCapacity(item)
- list.clearRetainingCapacity() void → list.clearRetainingCapacity()
- std.ascii.isPrint(c: u8) bool → std.ascii.isPrint('a')
- std.fmt.parseInt(comptime T: type, buf: []const u8, base: u8) ParseIntError!T → try std.fmt.parseInt(u8, s, 10)
- std.mem.writeInt(comptime T: type, buffer: *[@divExact(@typeInfo(T).int.bits, 8)]u8, value: T, endian: Endian) void → std.mem.writeInt(u8, buffer, value, .little)
- std.mem.findScalar(comptime T: type, slice: []const T, value: T) ?usize → if (std.mem.findScalar(u8, slice, value)) |v| _ = v
- std.math.minInt(comptime T: type) comptime_int → std.math.minInt(u8)
- std.Io.Writer.fixed(buffer: []u8) Writer → std.Io.Writer.fixed(&buf)
- w.buffered() []u8 → w.buffered()
- r.peek(n: usize) Error![]u8 → try r.peek(n)
- r.take(n: usize) Error![]u8 → try r.take(n)
- file.writer(io: Io, buffer: []u8) Writer → file.writer(io, &buf)
- dir.openFile(io: Io, sub_path: []const u8, options: OpenFileOptions) File.OpenError!File → try dir.openFile(io, s, .{})
- list.pop() ?T → if (list.pop()) |v| _ = v
- list.appendSlice(gpa: Allocator, items: []const T) Allocator.Error!void → try list.appendSlice(gpa, items)
- w.flush() Error!void → try w.flush()
