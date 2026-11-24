# INK-T2-S1 and INK-T2-STOCK integrity review notes (2026-11-24, sealed)

Two ablation arms of Inkling 2 Mini's I_T2, campaign `v5-thinking-first`,
RTX 5090, Basalt v0.5.7 (`400b411c`), harness9 tree `f38a29c7` (era
036638dd), T2's switches at `--jobs 1 --slots 1`, driven from the rig's
eval-host container (`openbeast-eval-host:8d1e28442569`, Linux) by
`~/openbeast-runs/ink-ablations-queue.sh`. Checkout on the rig:
`~/ob-ink2/.claude/worktrees/h9`. These notes are kept out of the public
docs (board.json and the reports are readable by a measured agent). The
first arm's review, whose procedure this repeats:
`ink-t2-integrity-2026-11-24.md`.

| Label | Board row | File (`evals/boards/v5/`) | Header time (UTC, naive) | End (campaign.log, UTC) | Rows |
|---|---|---|---|---|---|
| `I_T2_s1` | `INK-T2-S1` | `eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-s1-20261124-095656.json` (sha256 `c30d78a3…eaa4`) | 2026-11-24T09:56:56 | 2026-11-24T14:07:28 | 71 |
| `I_T2_stock` | `INK-T2-STOCK` | `eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json` (sha256 `0e018365…3000`) | 2026-11-24T14:07:40 | 2026-11-24T18:25:17 | 71 |

One file each: the campaign logs record `DONE I_T2_s1: 51/71 scorable` and
`DONE I_T2_stock: 53/71 scorable`, `lost: {}` in both state files, so no
rerun. The committed copies hash the same as the rig's files (read
2026-11-24 ~10:50 PST). `I_T2_s1` shares its campaign directory with the
running `I_UP8k` (`…/v5-thinking-first-inkling-2-mini-derisked-basalt-nvfp4-mx8/`);
only its `arm-I_T2_s1.log` and `campaign.log` were read.

## The scans (run 2026-11-24 ~10:45 PST, on the Mac, from copies)

The rig was read only (`I_UP8k` running in an eval container with Basalt
serving). `agents/logs/` (142 files: these two arms), the two results
files, the campaign files above, the queue log and the two saved Basalt
logs were copied with `tar` (mtimes kept) to `/tmp/inkab/rig/`; the
harness9 tree for the task texts is `git archive f38a29c7` (`/tmp/inkab/h9`).
From a worktree of origin/main at `be7e0a2c`:

```bash
L=/tmp/inkab/rig/ob-ink2/.claude/worktrees/h9/agents/logs
R=/tmp/inkab/rig/ob-ink2/.claude/worktrees/h9/evals/results
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir $L --until I_T2_s1=2026-11-24T14:07:28 \
    --json /tmp/inkab/scan-s1.json "I_T2_s1=$R/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-s1-20261124-095656.json@/tmp/inkab/h9"
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir $L --until I_T2_stock=2026-11-24T18:25:17 \
    --json /tmp/inkab/scan-stock.json "I_T2_stock=$R/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json@/tmp/inkab/h9"
```

**`I_T2_s1`: 71 transcripts, 71 units, 0 contaminated, exit 0.** Attempts
only, each refused by the eval guard, no other tool after them:

- `163_bigint_divmod_a` (FAIL): it18 and it23, `web_search` for Knuth's Algorithm D.
- `163_bigint_divmod_c` (FAIL): it9 `web_search` (Algorithm D), it10 `fetch` of the Wikipedia page on it.
- `163_bigint_divmod_e` (FAIL): it13, `web_search`, the same subject.
- `183_heston_little_trap` (FAIL): it6, `web_search` for the Albrecher "little trap" formulas.

**`I_T2_stock`: 81 transcripts, 71 units, 0 contaminated, exit 0.** The 10
extra transcripts are `I_T2_s1`'s last ten units (`agent-20261124-140544-*`
to `-140720-*`, 14:05:44 to 14:07:20 UTC, same task texts), picked up by the
window's start; the scan marks them unscored (the later transcript per unit
is the scored one), they are the same files `I_T2_s1`'s scan attributed,
and none has a hit. Attempts only:

- `163_bigint_divmod_c` (PASS): it5, `web_search` for Algorithm D, refused; it then solved without it.
- `163_bigint_divmod_e` (FAIL): it9, the same subject.
- `163_bigint_divmod_f` (FAIL): it49, the same subject.
- `183_heston_little_trap` (FAIL): it9, `web_search` for Lord-Kahl's rotation count.
- `192_pike_regex` (FAIL): it58, `web_search` for CPython's empty-match rule.

Score: `I_T2_s1` 51 raw, 51 scored; `I_T2_stock` 53 raw, 53 scored. No exclusion.

## The Linux shell gap, checked by hand

The agent's shell on Linux has no network deny (HARNESS_NOTES next-era (d)).
A script (`/tmp/inkab/scripts/shellgap.py`, not committed) walked every
`tool_call` of each arm's 71 scored transcripts (`I_T2_s1` 1,713 calls,
`I_T2_stock` 1,872), with the arguments parsed, for (a) any URL whose host is
not loopback (`(https?|ftp|git|ssh)://` outside `localhost`, `127.*`,
`0.0.0.0`, `[::1]`), and (b) in `bash` commands, `curl`, `wget`, `nc`,
`ncat`, `socat`, `telnet` at a command position, `pip install`, `git
clone|fetch|pull`, `cargo add|install|fetch`, `go get`, `go mod download`,
`urllib.request`, `requests.get|post`, `http.client`,
`socket.create_connection`, `socket.socket`, `/dev/tcp`, `/dev/udp`.

- `I_T2_s1`: 1 hit, the refused `fetch` above (163_c it10). No shell hit.
- `I_T2_stock`: 5 hits, all in one call (`168_linearizability_kv_e`, line
  53): a Rust variable named `nc` inside a quoted heredoc that writes the
  solution (`vec![…; nc]`, `nc: usize`). Not a command. No other hit.

**0 real hits in either arm.** None of the attempt units tried the shell
after the refusal. Clean.

## 122_gemm_blocked_d: the dlsym check, in both arms

Both rows read `FAIL: the multiply must be your own code: the binary imports
dlsym` (the Linux-host validator bug). Both solutions use
`std::thread::scope`. **Replay (docs/MODEL_CAMPAIGN.md, step 1), as PR #303
and #312:**

- **Rebuild.** Neither attempt wrote `gemm.rs` with a single heredoc: each
  wrote it with one `write_file` and then edited it with Python snippets run
  from bash (`python3 - <<'EOF' … EOF` or `python3 -c "…"`; str.replace or a
  slice splice on `gemm.rs`). A new `rebuild.py` re-runs exactly those
  snippets, in order, and compares every tool output that printed the file:
  - `I_T2_s1` (`agent-20261124-095935-9d011879.jsonl`): line 11 `write_file`
    (2,571 chars), edits at lines 14, 17, 24. Line 14's `grep -n` output
    matches exactly; line 17's `cat gemm.rs` output (the transcript keeps a
    tool result's first 2,000 characters) is a prefix of the rebuilt file.
    Final: 2,820 characters, sha256
    `133a6add88eb0cbf060406ba4158205e085b6ec698ad0cf861b855f872fcb08c`.
  - `I_T2_stock` (`agent-20261124-141254-3afcfaa6.jsonl`): line 17
    `write_file` (2,042 chars), edits at 53 (`{:.12e}` to `{:.12E}`; its
    `grep 'e}'` then found nothing, exit 1, as the rebuild does), 56 (back),
    65 and 71. Lines 56 and 65's `grep -n` outputs match exactly. Final:
    2,601 characters, sha256
    `6ecd157ff8a9ca1fb8fbe43c916afbf1c2f2ce9f4b17f0ce7e327f54e6ffbea2`.
  - No other call wrote the file (the rest compiled it, ran it, and ran the
    agent's own Python checks).
- **Replay directory on the rig:** `~/ob-cal/.claude/gemm-replay-ink-ab/`
  (new; #303's and #312's directories untouched): `rebuild.py`, `tx/` (the
  two transcripts) and `replay.py`, a copy of #312's whose `grade` is
  unchanged and whose `rebuild` calls `rebuild.py`. Run from `~/ob-cal`
  (`~/ob-cal/.claude/worktrees/h9` at `f38a29c7`, clean), in a new
  eval-host container per step, niced, CPU-capped (the running `I_UP8k`
  container and Basalt were not touched; the new container's `/tmp` is its
  own, so the `/tmp/eval_gemm` fixture cannot collide):

  ```bash
  OPENBEAST_EVAL_CONTAINER_CPUS=8 nice -n 19 scripts/eval-host-container.sh run -- \
      nice -n 19 python3 .claude/gemm-replay-ink-ab/replay.py rebuild
  OPENBEAST_EVAL_CONTAINER_CPUS=8 nice -n 19 scripts/eval-host-container.sh run -- \
      nice -n 19 python3 .claude/gemm-replay-ink-ab/replay.py grade s1 --original   # then without --original, x3; same for stock
  ```

  `rebuild` in the container gave the same two sums (18:52:46Z). Grading
  ran 18:52:51 to 18:53:12 UTC, at timeout scale 2.0.
- **Control, the unpatched validator:** `VERDICT s1 [original rule]: FAIL`
  and `VERDICT stock [original rule]: FAIL`, both `the binary imports dlsym`,
  as the runs recorded.
- **Proposed rule:** `REPLAY-RULE: allowed dlsym (Rust ELF; no other loader
  or BLAS import)`, `OK: 6 hidden cases`, **PASS in 3 of 3 runs for each**.
  The patched validator written is byte-identical to #303's and #312's
  (`validate_d_proposed.py`, sha256 `db11379935ae7177…`).

So `exclusions.json` carries a `validator_notes` entry for each run, with
INK-T2's wording. Not adjusted: under the proposed rule INK-T2-S1 would be
52/71 and INK-T2-STOCK 54/71.

## The empty capped turns (Basalt #588), now with the server's count

`evals/audit/capped_turns.py /tmp/inkab/scan-<arm>.json`:

| | I_T2_s1 | I_T2_stock |
|---|---:|---:|
| Turns | 1,911 | 2,032 |
| Ended at the cap (`length`) | 240 | 190 |
| Empty (no answer, no call) | **167** in 17 units | **105** in 12 units |
| Of them in failed units | 144, in 15 of 20 fails | 102, in 10 of 18 fails |
| Text only | 0 | 2 |
| Still carrying one tool call | 73 | 83 |

This time the Basalt log was kept (`basalt-logsaver.sh`, `docker logs -f
--timestamps` from the container's start), and
`evals/audit/basalt_log.py` over each arm's window counts the same
requests, cap endings and empty turns as the transcripts: 1,911 / 2,032
requests, 240 / 190 `end=length`, 167 / 105 "max tokens reached while
thinking, no answer" warnings, and the runner's completion and prompt
token sums exactly (4,959,068 / 64,747,685 and 4,900,235 / 64,862,866).
The queue's own grep before each stop logged 167 and 105. So the
transcript counter is confirmed on two arms; the lenient-parse split
(XML-in-text versus a cut end marker) is still not in the log.
Not contamination; a serve property.

## Follow-up

- `I_UP8k` (harness10 tree, `~/ob-ink2`, transcripts in
  `~/ob-ink2/agents/logs/`) gets the same scan, shell check,
  `capped_turns.py`, `basalt_log.py` on
  `basalt-20261124-102527-c9c29fce36b3.log`, and the replay for a Rust GEMM
  stopped at the dlsym check, once it ends.
