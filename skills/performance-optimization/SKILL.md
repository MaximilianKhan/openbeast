---
name: performance-optimization
description: Measure-driven performance work. Activate when the user asks to make something faster, when you spot what looks like a perf issue, or when adding code in a hot path. Encodes the discipline frontier models follow: profile first, optimize the actual hotspot, re-measure, never speculate.
allowed_tools: [bash, read_file, edit_file, grep]
recommends_subagent: false
---

# Performance optimization

Most "optimization" makes things worse, slower to read, or both. The reason is
simple: people optimize what they THINK is slow rather than what's actually
slow. The actual hotspot is rarely where intuition says it is.

The discipline:

1. **Define what "fast enough" means** (measurable, with units)
2. **Measure** the current performance
3. **Profile** to find the actual hotspot
4. **Change one thing** in the hotspot
5. **Re-measure** to confirm the change helped
6. **Repeat** until target met or diminishing returns

Skip step 1 → you'll never know when to stop.
Skip step 3 → you'll optimize the wrong code.
Skip step 5 → you may have made it worse and not know.

## Step 1 — Define the target

Without a target, "faster" is a moving goalpost. Make it concrete:

- "P99 request latency under 100 ms at 1000 RPS"
- "Build wall-clock under 30 s on the dev laptop"
- "Memory under 500 MB during processing of the standard test corpus"
- "Throughput of 10K events/s on a single core"

Specific number, specific condition, specific machine class. Then you can
measure and know whether you're done.

If the user said "make it faster" without a target, **ask** or **set one
yourself and document**: "Targeting 2x throughput on the standard test;
proceed unless told otherwise."

## Step 2 — Measure (baseline)

Always capture a baseline before changing anything. Without it, you can't
prove you helped.

Tools:

```bash
# Wall-clock + memory of a process
/usr/bin/time -v <command>           # Linux
/usr/bin/time -l <command>           # macOS

# Lots of runs for stable numbers
hyperfine --warmup 3 'python3 script.py'

# Built-in language tools
python3 -m timeit -s "from X import f" "f()"
go test -bench=. -benchmem ./...
```

Capture: wall-clock, CPU time, peak memory, and any other relevant metric
(throughput, latency percentiles). Run 3+ times and report median; one run
is noise.

## Step 3 — Profile (find the actual hotspot)

This is the step everyone skips. Don't.

### Python

```bash
# CPU profile
python3 -m cProfile -o profile.out script.py
python3 -c "import pstats; pstats.Stats('profile.out').sort_stats('cumtime').print_stats(20)"

# Or sampling profiler — better for long runs
py-spy record -o profile.svg -- python3 script.py
py-spy top -- python3 script.py    # live view

# Memory
mprof run script.py && mprof plot
```

### Go

```bash
# Built-in
go test -cpuprofile=cpu.out -bench=.
go tool pprof -top cpu.out

# Live trace
go test -trace=trace.out -bench=.
go tool trace trace.out
```

### C / C++

```bash
# Sampling
perf record -F 99 ./prog
perf report

# Instrumented (more accurate, slower)
valgrind --tool=callgrind ./prog
kcachegrind callgrind.out.<pid>

# Memory
valgrind --tool=massif ./prog
```

### Web / API

- Instrument the actual production-like load (locust, k6, vegeta)
- Measure at the boundaries: client-perceived latency, server-side
  processing time, DB time, network time. The bottleneck is rarely "the
  code" — it's often "the DB" or "the network."

### What to look for in the profile

- **Functions consuming >5% of total time.** These are the candidates.
- **Surprises.** "Why is `serialize_json` taking 30%?" → that's the lead.
- **Loops with high iteration counts × non-trivial body.** O(n²) hides
  here.
- **Allocation sites.** Hot loops doing `malloc` / `new` / boxing in the
  inner body are red flags.

If your profile says the bottleneck is `string_format` or `json_encode`,
optimizing the algorithm three layers up doesn't help. Always optimize at
the level the profiler points to.

## Step 4 — Change one thing

ONE thing. Resist the urge to apply five optimizations at once — you won't
know which helped, which hurt, and you may have tangled the logic.

Common high-leverage changes (in rough order of impact):

### Asymptotic — biggest wins, easiest to reason about

- Replace O(n²) with O(n log n) or O(n)
- Add an index / hash table to eliminate linear scans
- Cache invariants computed in a loop
- Memoize repeated calls with same args

### Allocation — second-biggest, surprisingly often the issue

- Reuse buffers instead of allocating per iteration
- Preallocate to known size (`list = [None]*n`, `vec.reserve(n)`)
- Avoid intermediate copies (`.join` instead of `+=` in a loop, slicing
  carefully)
- For Go: avoid escape to heap (run with `-gcflags="-m"` to see)
- For C++: pass by const-ref, return by RVO, avoid `new`/`delete` in hot loops

### I/O — gigantic wins if I/O-bound

- Batch reads / writes (block size matters; 64K-1M typical sweet spot)
- Move serialization to a faster format (msgpack/protobuf > JSON)
- Connection pooling (don't reconnect per request)
- Async / parallel I/O (genuine parallelism for I/O-bound, threading for
  Python's GIL workloads is fine)

### Cache locality — hardware-level wins

- Process data in chunks that fit in cache (typically 32-64K for L1)
- Use struct-of-arrays vs array-of-structs based on access pattern
- Match access pattern to memory layout (row-major iteration of row-major
  arrays)
- For matrix operations: blocked algorithms (see eval task 122)

### Parallelism — last resort, easiest to get wrong

- Trivially parallel (independent items): `multiprocessing.Pool`,
  goroutines, OpenMP
- Pipeline parallelism (stages can overlap): channels, futures
- Watch for: false sharing, lock contention, GIL (in Python), Amdahl's law

## Step 5 — Re-measure

After the change, run the SAME measurement as the baseline. Same machine,
same input, same number of repetitions.

Compare:
- Did the metric improve enough to be worth the change?
- Did any other metric regress? (CPU might be down but memory up.)
- Is the new code clearer / equivalent / acceptably more complex?

If the change helped: keep it, commit it cleanly, move on.
If the change didn't help: revert. Don't keep "harmless" changes — they
make the code harder to read for no benefit.

## Step 6 — Repeat or stop

Re-profile. The hotspot has probably moved. Optimize the new top item or
declare done.

Stop when:
- Target met
- Diminishing returns (next 1% of speedup costs 50% more code complexity)
- Profile shows the remaining time is in code you can't change (libraries,
  syscalls, hardware)

## Anti-patterns

- **Speculative optimization.** "I'll just rewrite this in C, surely
  that'll help." Did you profile? No? Stop.
- **Micro-optimization without measurement.** Replacing `len(x) == 0` with
  `not x` because someone said it's faster. The compiler already knows.
  Spend your time on bigger fish.
- **Optimizing test setup instead of system under test.** "My benchmark
  takes 30s" — that's your benchmark setup, not the system. Profile what
  you're actually shipping.
- **Optimizing without a target.** You'll never stop, and the code gets
  unreadably clever.
- **Cargo-culting "fast" patterns.** "Always use `numpy` for arrays" —
  not if your array is 10 elements; the FFI overhead exceeds the work.
- **Multi-change commits.** "I refactored the loop AND added a cache AND
  switched the data structure." Now if perf got worse, you can't bisect.

## Specific common wins

Quick reference for diagnoses:

| Symptom | Likely cause | First thing to try |
|---|---|---|
| Slow on big inputs, fine on small | O(n²) | Hashtable lookup, sort first |
| Memory grows with input size | Caching / accumulation | Bound the cache, stream |
| Slow even on tiny inputs | Setup cost | Check init / first-call costs |
| CPU pegged, output rate low | Computation-bound | Profile; algorithmic win |
| CPU idle, output rate low | I/O-bound | Batch, async, pool |
| Slow only at 99th percentile | GC pause / lock contention / cold cache | Trace, not just profile |
| Slow only sometimes | Resource contention, throttling | Reproduce; diff env |

## Working with the eval suite

Tasks 22, 39, 122, 145, 159 in our eval suite all test perf-flavored work.
The validation includes hard time gates — your reference impl needs to be
careful about asymptotic. See `eval-task-author` SKILL pitfall #5 for how
those gates are calibrated.

## Done criteria

A good optimization:
- [ ] Target was defined upfront
- [ ] Baseline was captured (with units, conditions)
- [ ] Profile identified the actual hotspot
- [ ] One focused change, in the hotspot
- [ ] Re-measurement confirms improvement
- [ ] Improvement is worth the complexity cost
- [ ] If you can't show a measurable improvement, revert
