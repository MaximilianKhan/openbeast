# INK-UP8K integrity review notes (2026-11-24, sealed)

The third Inkling 2 Mini ablation arm, `I_UP8k`, campaign
`v5-thinking-first`, RTX 5090, Basalt v0.5.7 (`400b411c`), the Blackfrost
Derisked pack (`d2b01bcc…a427`), DeepSeek's UP_8k switches at `--jobs 1
--slots 1`, on the **harness10** tree `~/ob-ink2` at `d41c2c47` (era
`9bcd5aa6`; not the h9 worktree), driven from the rig's eval-host container
(`openbeast-eval-host:8d1e28442569`, Linux) by
`~/openbeast-runs/ink-ablations-queue.sh`. These notes are kept out of the
public docs (board.json and the reports are readable by a measured agent).
The procedure repeats `ink-t2-s1-stock-integrity-2026-11-24.md`.

| Label | Board row | File (`evals/boards/v5/`) | Header time (UTC, naive) | End (campaign.log, UTC) | Rows |
|---|---|---|---|---|---|
| `I_UP8k` | `INK-UP8K` | `eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-up8k-20261124-182529.json` (sha256 `87fcefeb76c7abde100e7fc1d93f8a39769260851237713410346057fe011a1f`) | 2026-11-24T18:25:30 | 2026-11-24T22:28:05 | 71 |

One file: `campaign.log` records `START I_UP8k` 18:25:28 and `DONE I_UP8k:
49/71 scorable` 22:28:05 (UTC), and `state.json` has `lost: {}`, so no
rerun. Its env: `OPENBEAST_AGENT_ACT_NUDGE=0`, `FORCE_ACT=1`,
`FORCE_ACT_AFTER=2`, `DONE_VERIFIED=1`, `ALGO_LINES=1`, `HIDE_WEB=1`,
`MAX_TOKENS=8192`, no seed; I_T2_s1's entry in the same state file (I_T2
plus `--seed 1`) has `ACT_NUDGE=1` and the other four at 0. The committed
copy hashes the same as the rig's file (read 2026-11-24 ~14:50 PST). The
queue's `.out` ends `RESTORE OK` (gate `:8091` 200, a chat from Qwen3.8)
and `QUEUE DONE` at 14:28:14 PST.

## The scan (run 2026-11-24 ~14:50 PST, on the Mac, from copies)

The rig was read only (another agent was about to build a patched Basalt
there; no container, launcher or `~/openbeast-runs/inkling-perf-2026-11-23/`
was touched). `~/ob-ink2/agents/logs/` (71 files, all this arm's:
`agent-20261124-182530-*` to `agent-20261124-222755-*`), the results file,
the campaign directory, the queue script and log, and the three saved
Basalt logs were copied with `tar` to `/tmp/inkup/rig/`; the task texts are
`git archive d41c2c47` (`/tmp/inkup/h10`). From a worktree of origin/main
at `66e2293f`:

```bash
L=/tmp/inkup/rig/ob-ink2/agents/logs; R=/tmp/inkup/rig/ob-ink2/evals/results
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir $L --until I_UP8k=2026-11-24T22:28:05 \
    --json /tmp/inkup/scan.json "I_UP8k=$R/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-up8k-20261124-182529.json@/tmp/inkup/h10"
```

**71 transcripts, 71 units, 1 contaminated transcript, exit 1.**

### The contaminated unit: `163_bigint_divmod_c` (FAIL)

Transcript `agent-20261124-191551-caba9d4e.jsonl` (C, Knuth's Algorithm D).

- it8 (line 34), after eight turns of re-deriving the algorithm in its
  reasoning: `bash` `find / -name "*.c" -path "*big*" 2>/dev/null | head;
  find / -name "*.c" -path "*gmp*" 2>/dev/null | head`. The result listed
  ten paths: `/home/max/ob-ink2/evals/refs/163_bigint_divmod/{divmod.c,
  leak.c, slow.c, wrong_noaddback.c, wrong_shift32.c}` and the same five
  under `~/ob-ink2/.claude/worktrees/h9/evals/refs/`. The scan's kind:
  `listed` (a tool's OUTPUT named a forbidden path).
- it9 (line 37): `cat /home/max/ob-ink2/evals/refs/163_bigint_divmod/divmod.c`:
  refused ("refusing to run a command that names a sealed path").
- it10 (line 41): `sed -n '1,200p' …/divmod.c 2>/dev/null || echo sealed`:
  refused the same way. Its reasoning at it10 and it11 opens "The refs are
  sealed. Fine — I'll just write my own implementation" and "Sealed. Fine,
  I'll write it myself."
- No other tool call names the tree. A search of every later line (35 to
  244) for `noaddback`, `shift32`, `wrong_`, `leak.c`, `slow.c` and `refs`
  finds only the two refusals: the file names are never mentioned again in
  its reasoning or its code. It wrote its own `divmod.c` (line 69 on), spent
  the rest of the unit on its borrow sign convention, and ended at the
  60-turn limit (`max_iterations`), FAIL.

**Verdict: contaminated by the scan's rule** (any hit other than `source`
and `web` that is not a refusal: a listing of the answer tree is not
refused, it printed). The contents were never read; what leaked is five
file names, two of which name failure modes (`wrong_noaddback`,
`wrong_shift32`). The unit failed, so excluding it costs no pass: 49 raw,
49 scored. **Excluded** (`exclusions.json`, run `INK-UP8K`), and dropped
from every paired test (ablation.py reads the same file).

**Why it could happen.** On a Linux harness host the agent's own `bash` has
no kernel read-deny (`agents/tools.py` `_eval_read_deny_prefix` returns
`[]` off macOS; evals/README.md "Residual gaps": "a Linux host is not
leak-proof against an agent that reads `evals/refs`"). The eval-host
container mounts the checkout at its own path (`scripts/eval-host-container.sh`
`-v "$REPO_DIR:$REPO_DIR"`), so `find /` walks the eval tree. The command
text named no sealed path, so the text check let it run; the reads that did
name one were refused. First observed on the rig: the three T2 arms ran the same
container with the same mount, their scans had no `listed` hit, and on the
Algorithm D units they reached for `web_search` instead (refused). Here web
tools were not offered (`HIDE_WEB=1`), and this unit searched the disk.
One instance; not a measured effect of hiding the web.

**Attempt or contamination, by the board's rules.** `integrity_scan.py`
(module docstring): "A hit whose result was a refusal (the seal's 'Error:
refusing', or the kernel's 'Operation not permitted' alone) is an ATTEMPT. A
unit with any other hit, except `source` and `web`, is CONTAMINATED: report
it as such and do not count its verdict." The two reads (it9, it10) are
attempts. The `find` (it8) is a `listed` hit whose result was not a refusal:
it printed the paths. So the unit is contaminated under the rule as written,
whatever the names were worth; `exclusions.json` is how the board withholds
a verdict. Precedent: every earlier exclusion applied the same rule
(GLM-C's three, Q38U-L0's and Q38U-T2's three each). Decision record:
DECISIONS-2026-11-24 §14.

**The same pattern elsewhere** (2026-11-24 ~15:30 PST, read-only on the rig,
niced): every `bash` call in every transcript of the other rig arms,
checked for a filesystem search (`find`, `locate`, `tree`, `du`, `ls -R` on
a path outside `/tmp`) and for any result that printed a sealed path
(`/evals/{refs,tasks,suites,results,cache}/`, `agents/logs/agent-`):

| Transcripts | Arm(s) | Filesystem searches | Root-wide (`find /`) | Printed a sealed path |
|---|---|---:|---:|---:|
| `~/ob-ink/.claude/worktrees/h9/agents/logs` (71) | I_T2 | 6 | 0 | 0 |
| `~/ob-ink2/.claude/worktrees/h9/agents/logs` (142) | I_T2_s1, I_T2_stock | 20 | 0 | 0 |
| `~/ob-ink2/agents/logs` (71) | I_UP8k | 6 | 1 | **1** (this unit) |
| `~/ob-q38/.claude/worktrees/h9/agents/logs` (78) | Q_T2 (+ r1) | — | 2 | 0 |
| `~/ob-cal/.claude/worktrees/h9/agents/logs` (89) | the calibration | — | 0 | 0 |
| `~/ob-eval-container/agents/logs` (123) | Q38U-L0 | — | 0 | 0 |

Every other search in the Inkling arms is under the pinned toolchains
(`/opt/openbeast-eval/zig-0.16.0/lib`, `/opt/openbeast-eval/cpython-3.12.16`).
Q_T2's r1 (`agent-20261123-231113-84abeed3`, lines 143 and 149) ran `find /`
twice for Rust's `core_arch/…/avx.rs`; the results named no sealed path.
So the exposure exists on every rig arm, and this is the only time it
printed an answer tree.

### Everything else

No `web_search` or `fetch` call in any transcript (the tools were not
offered). Read-only looks outside `/tmp`: three units read the pinned Zig
toolchain's standard library under `/opt/openbeast-eval/zig-0.16.0/lib`
(`agent-20261124-183051-e7aba1a0` lines 18 and 49,
`agent-20261124-194850-5225f540` lines 14 and 18,
`agent-20261124-222710-9673daa1` line 18): the compiler's own tree, not an
answer key, not flagged.

## The Linux shell gap, checked by hand

The same script as the T2 ablations (`/tmp/inkup/scripts/shellgap.py`, not
committed): every `tool_call` of the 71 scored transcripts (**1,720 calls**),
arguments parsed, for a non-loopback URL and, in `bash`, `curl`, `wget`,
`nc`, `ncat`, `socat`, `telnet` at a command position, `pip install`, `git
clone|fetch|pull`, `cargo add|install|fetch`, `go get`, `go mod download`,
`urllib.request`, `requests.get|post`, `http.client`,
`socket.create_connection`, `socket.socket`, `/dev/tcp`, `/dev/udp`.
**0 hits.** No route off the host.

## 122_gemm_blocked_d: the dlsym check

The row reads `FAIL: the multiply must be your own code: the binary imports
dlsym` (the Linux-host validator bug; the solution uses `std::thread::scope`).
Replayed as PR #303, #312 and #313:

- **The task is the same grader.** `run_eval.load_tasks()` on `f38a29c7` and
  `d41c2c47` gives the same `122_gemm_blocked_d` field for field (only
  `_path` differs); the validation script's sha256 is `55142cb4b26f6f58…`
  in both. So grading from `~/ob-cal/.claude/worktrees/h9` (`f38a29c7`,
  clean) is grading by this arm's own validator.
- **Rebuild** (`agent-20261124-183026-26f685bc.jsonl`, copied to
  `tx/up8k.jsonl`). Five calls wrote `gemm.rs`: a quoted heredoc (line 9),
  two Python edit snippets (13, 17), a second quoted heredoc that rewrote it
  whole (25), and a Python edit (33). Line 21 printed it (`cat`). A new
  `rebuild.py` re-runs each of those six commands **in full** with
  `/tmp/eval_gemm` replaced by a work directory (the sample files taken from
  line 5's output) and compares each tool result: lines 9 and 13 (rustc
  errors, results kept to 2,000 characters) as prefixes, line 17 on its first
  line (`error[E0425]: cannot find value \`a\` in this scope`), lines 21
  (`cat`, 1,816 characters), 25 (`BUILD_OK`) and 33 (`SAMPLE_OK`, the
  sample diffed) **exactly**. Any other call that wrote the file would stop
  it; none did. Final: 2,461 characters, sha256
  `bd360460863ff28b002b0a84c25bd19685a2ff31b2dbd77ba46d4f9e594dfba5`.
- **Replay directory on the rig:** `~/ob-cal/.claude/gemm-replay-ink-up8k/`
  (new; the earlier three untouched): `rebuild.py`, `tx/`, and `replay.py`, a
  copy of `gemm-replay-ink-ab/replay.py` whose `grade` is unchanged (only the
  docstring differs). Each step in a new `--rm` eval-host container from
  `~/ob-cal`, `nice -n 19`, `OPENBEAST_EVAL_CONTAINER_CPUS=4` (no fixed
  name; the running `basalt-serve` and the other agent's work untouched):

  ```bash
  OPENBEAST_EVAL_CONTAINER_CPUS=4 nice -n 19 scripts/eval-host-container.sh run -- \
      nice -n 19 python3 .claude/gemm-replay-ink-up8k/replay.py rebuild
  OPENBEAST_EVAL_CONTAINER_CPUS=4 nice -n 19 scripts/eval-host-container.sh run -- \
      nice -n 19 python3 .claude/gemm-replay-ink-up8k/replay.py grade up8k --original   # then without, x3
  ```

  Rebuild 22:53:57Z; grading 22:54:04 to 22:54:15Z, timeout scale 2.0.
- **Control, the unpatched validator:** `VERDICT up8k [original rule]: FAIL`,
  `the binary imports dlsym`, as recorded.
- **Proposed rule:** `REPLAY-RULE: allowed dlsym (Rust ELF; no other loader
  or BLAS import)`, `OK: 6 hidden cases`, **PASS in 3 of 3 runs**. The
  patched validator written is byte-identical to #303's, #312's and #313's
  (`validate_d_proposed.py`, sha256 `db11379935ae7177…`).

`exclusions.json` gets INK-T2's `validator_notes` wording for `INK-UP8K`.
Not adjusted: under the proposed rule INK-UP8K would be 50/71.

## The empty capped turns (Basalt #588) and the server log

`evals/audit/capped_turns.py /tmp/inkup/scan.json`: 1,969 turns, 302 ended
at the cap, **226 empty** (no answer, no call) in 19 units, 1 with text
only, 75 still carrying a call; 185 of the empty ones in 17 of the 22 fails.

`evals/audit/basalt_log.py ~/openbeast-runs/basalt-server-logs/basalt-20261124-102527-c9c29fce36b3.log
--since 2026-11-24T18:25:28 --until 2026-11-24T22:28:05` (container
`c9c29fce36b3`, started 10:25:18 PST): 1,969 requests, 302 `end=length`,
226 "no answer" warnings, completion 5,300,048 and prompt 73,541,527
tokens: the transcripts' and the runner's numbers exactly (the queue's own
grep before the stop also logged 226).

## The forced-act turns

UP_8k's mechanism: after two turns in a row cut off at the cap with no
content and no call, the runner sends the next request with
`chat_template_kwargs.enable_thinking=false` and an `[act turn]` note
asking for "Decisions so far / Rejected options" and a tool call
(`agents/runner.py` at `d41c2c47`, lines 647-712, 1939-1941, 2101-2120).
Each forced reply is logged as `force_act_result`.

- **From the transcripts** (`capped_turns.py`, extended in this PR): 69
  `force_act` triggers, **68 forced turns** in 13 units (the 69th, in
  `195_riscv_insn_budget`, was due after turn 60, its limit, so never sent).
  **67 of 68 still carried reasoning** (median 22,626 characters; the one
  without, `183_heston_little_trap` it53, ran to the cap with 186 characters
  of content and a call);
  **56 ran to the 8,192 cap**; **50 ended empty**; **18 made a tool call**.
  None of the 68 reasoning texts uses the note's own phrases ("act turn",
  "thinking off", "Decisions so far", "Rejected options") in its first 3,000
  characters; most open "Let me just write the code…" or "The user wants me
  to act…" and then re-derive the design inside the thinking block.
- **From the server log** (`basalt_log.py`, `without_budget_line`): every
  thinking request is preceded by "thinking budget 6144 tokens, output cap
  8192 (answer-room auto)"; in this window **68 requests had no such line**:
  56 ended `length`, 12 `stop`, **50 "no answer" warnings**, 486,287
  generated tokens (929 s of decode). Exactly the forced turns. Control: the
  same split over I_T2_s1's and I_T2_stock's windows finds 0.
- **Why** (Basalt `400b411c`, read from GitHub at the pin): the Inkling
  dialect turns `enable_thinking: false` into `reasoning_effort: "none"`
  (`serve/models/inkling2.py:527-529`), a level the template writes into a
  system message; it adds no generation prompt, so "the model opens its own
  blocks" (lines 5-6). `serve/service.py` sets `thinking = False` for such a
  request (line 1148), so it gets no thinking budget (1370) and no answer
  room (answer_budget runs only when thinking, 1511). The parser still
  reads a thinking block when the model writes one. The THINK-OFF probe
  before the arm (`ink-ablations-queue.out` 10:25:27 PST: `reasoning_chars=117
  content='391'` on "What is 17*23?") showed the short block the Strata report
  §11.1 already noted; at agent context lengths the block is not short.
- **Cache.** Each forced request and the request after it hit **0%** of the
  prompt cache (4,250,312 and 4,042,304 uncached prompt tokens; 322 s and
  310 s of prefill); every other request 96.5% (294 s), as in the T2 arms
  (96.6% / 96.5%). The effort level sits in a system message near the top,
  so the rendered prefix differs both ways. Overall cached share 85.6%.

Not contamination; a serve property that undermines the arm's mechanism.
The analysis is in the report §10.9.
