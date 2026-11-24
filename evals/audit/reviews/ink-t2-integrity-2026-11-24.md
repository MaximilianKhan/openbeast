# INK-T2 integrity review notes (2026-11-24, sealed)

The run: Inkling 2 Mini Derisked (Basalt NVFP4-MX8), campaign
`v5-thinking-first` arm `I_T2` (T2's switches at `--jobs 1 --slots 1`),
RTX 5090, Basalt v0.5.7 (`400b411c`), harness9 tree `f38a29c7` (era
036638dd), driven from the rig's eval-host container
(`openbeast-eval-host:8d1e28442569`, Linux). Checkout on the rig:
`~/ob-ink/.claude/worktrees/h9`. Board row `INK-T2`. These notes are kept
out of the public docs (board.json and the reports are readable by a
measured agent).

| Label | File (`evals/boards/v5/`) | Header time (UTC, naive) | End (campaign.log, UTC) | Rows |
|---|---|---|---|---|
| `I_T2` | `eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json` | 2026-11-24T04:58:43 | 2026-11-24T09:29:33 | 71 |

One file: the campaign recorded `DONE I_T2: 49/71 scorable` with no lost
unit, so there was no rerun.

## The scan (run 2026-11-24 ~01:37 PST, on the Mac, from copies)

The rig was read only (a benchmark and three ablation arms were about to
use it). The transcripts (`agents/logs/`, 71 files), the results directory
and the campaign directory were copied with `rsync -a` (mtimes kept) to
`/tmp/ink/`; the harness9 tree for the task texts is `git archive
f38a29c7` (`/tmp/ink/h9`). From a worktree of origin/main at `b6584f9a`:

```bash
R=/tmp/ink/results/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir /tmp/ink/logs \
    --until I_T2=2026-11-24T09:29:33 --json /tmp/ink/scan.json "I_T2=$R@/tmp/ink/h9"
```

`--tz UTC`: the container writes naive UTC timestamps. `--until`: the END
line of the campaign log. On the rig the same command works with
`--logs-dir ~/ob-ink/.claude/worktrees/h9/agents/logs`, `@~/ob-ink/.claude/worktrees/h9`,
and `$R` replaced by the rig's file or the committed copy (the `--until`
value carries the end, so a reset mtime does not matter).

Result: `I_T2`: 71 transcripts, 71 units, 0 unattributed, **0
contaminated**, exit 0. Attempts only (`web_search`, each refused by the
eval guard; no other tool followed):

- `163_bigint_divmod_a` (FAIL): it17, a query for Knuth's Algorithm D.
- `163_bigint_divmod_d` (FAIL): it5 and it8, the same subject.
- `163_bigint_divmod_f` (FAIL): it6, the same subject.
- `183_heston_little_trap` (FAIL): it13, the Heston characteristic function.

Score: 49 raw, 49 scored. No exclusion.

## The Linux shell gap, checked by hand

On Linux the agent's shell has no network deny (HARNESS_NOTES next-era
(d)), and the scan's `network` kind matches curl / wget / git remotes. A
wider pass over every `tool_call` in the 71 transcripts looked for (a) any
URL whose host is not loopback (`(https?|ftp|git|ssh)://` outside
`localhost`, `127.*`, `0.0.0.0`, `[::1]`) in any tool's arguments, and
(b) in `bash` commands: `curl`, `wget`, `nc `, `ncat`, `pip install`,
`git clone`, `cargo add|install|fetch`, `go get`, `go mod download`,
`urllib.request`, `requests.get`, `socket.create_connection`, `/dev/tcp`.
**0 hits.** None of the four units above tried the shell after the refusal
(the pattern behind Q38U-L0's and Q38U-T2's six exclusions). Clean.

## The empty capped turns (Basalt #588) and the lenient parser

The Basalt server log, which names each "max tokens reached while
thinking, no answer" reply, went with the container: `inkling-v5.sh stop`
is `docker rm -f basalt-serve` (2026-11-24 01:29:33 PST,
`~/openbeast-runs/ink-I_T2-run.out`), and the daemon's logging driver is
`json-file` (`docker info`), so no copy survives. The count is from the
transcripts with `evals/audit/capped_turns.py /tmp/ink/scan.json`: 1,935
turns, 293 ended `length`; **203 ended empty at the 8,192 cap** (an
`output_cap` event with `reasoning_chars`: no answer text and no call) in
21 units, 199 of them in 17 of the 22 failed units; 2 capped turns had
answer text; 88 capped turns still carried one tool call. Per unit: the
board note and the report. Q38U-T2 (llama.cpp, same arm and tree), counted
the same way over its board rows (the main file with its 7 lost units
replaced by r1): 2,037 turns, 311 at the cap, 305 empty in 41 units, 5
with a call. Not contamination; recorded as a serve property.

## 122_gemm_blocked_d: the dlsym check (docs/MODEL_CAMPAIGN.md procedure)

The row reads `FAIL: the multiply must be your own code: the binary imports
dlsym`. **Replay (step 1 of the procedure)**, as PR #303:

- Final source from the transcript `agent-20261124-050051-dc8fa458.jsonl`:
  one `bash` call (line 10) wrote `/tmp/eval_gemm/gemm.rs` with a quoted
  heredoc (`<< 'EOF'`, no expansion) and built it; no later call wrote the
  file (the rest ran the binary and the agent's own Python checks;
  `harness.diag_exposure` has no entry for the unit, as no file tool
  wrote it). 2,333 characters, sha256
  `899eaeaf91df105a4eaa5d668f27ec36946cf2d1736896296b7cebcc5c15d70a`; it
  uses `std::thread::scope`, so std imports `dlsym` on Linux.
- Replay directory on the rig: `~/ob-cal/.claude/gemm-replay-ink/`, a copy
  of PR #303's `replay.py` restricted to this attempt (`it2`), with one
  addition: a `heredoc` shell edit, re-run exactly (`cat > gemm.rs <<
  'EOF' … EOF` in the work dir). PR #303's directory was not touched.
  `rebuild` in the eval-host container gave the same sha256 (`899eaeaf…`).
- `grade` ran the harness9 tree's own lifecycle (`~/ob-cal/.claude/worktrees/h9`
  at `f38a29c7`, clean) in `openbeast-eval-host:8d1e28442569` through
  `scripts/eval-host-container.sh run -- nice -n 10 python3 …`, at timeout
  scale 2.0, 2026-11-24 09:35:38 to 09:35:48 UTC (GPU idle, the ablation
  queue waiting on the Maya benchmark; about 10 s of CPU in all). The
  patched validator it wrote is byte-identical to PR #303's
  (`validate_d_proposed.py`, sha256 `db11379935ae7177…`).
- **Control, the unpatched validator:** `VERDICT it2 [original rule]: FAIL`,
  `the binary imports dlsym`, as the run recorded.
- **Proposed rule:** `REPLAY-RULE: allowed dlsym (Rust ELF; no other loader
  or BLAS import)`, then `OK: 6 hidden cases`, **PASS in 3 of 3 runs** (the
  N = 2048 case's seed is fresh each run).

So `exclusions.json` carries the `validator_notes` entry (run `INK-T2`):
"a stop at the binary check on std's dlsym (Linux host): verdict not
measured; replayed under the proposed next-era rule (docs/HARNESS_NOTES.md):
verdict PASS, 6 hidden cases". The score is not adjusted: the proposed rule
is not the suite's rule until the next era. Under it the row would be 50/71.

## Follow-up

- The three queued ablation arms (`I_T2_s1`, `I_T2_stock`, `I_UP8k`) get
  the same scan, `capped_turns.py` and, for any Rust GEMM stopped at the
  dlsym check, the same replay.
- The ablation queue logs each arm's server-side "no answer" count before
  it stops Basalt (`ink-ablations-queue.sh` line 200), a cross-check for
  `capped_turns.py`; it does not keep the full log, so the lenient-parse
  paths stay uncountable there too. Saving `docker logs basalt-serve` to a
  file before every stop is the fix; the rig's scripts were not edited
  (read-only session); noted in docs/TODO.md.
