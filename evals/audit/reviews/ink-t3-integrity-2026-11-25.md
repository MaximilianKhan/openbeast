# INK-T3 integrity review notes (2026-11-25, sealed)

The improved Inkling 2 Mini arm `I_T3`, campaign `v5-thinking-first`, RTX
5090, Basalt v0.5.7 (`400b411c`) plus OpenBeast's local patch of upstream PR
#632 (head `55f9be17`, patch body sha256 `af15ee07…c0cd`; served from
`~/openbeast-runs/inkling-perf-2026-11-23/build/basalt-tree-pr632`), Basalt's
stock pack (`28ec7072…1b99`), `--parallel 1 --max-context 262144 --kv fp8
--answer-room 8192`, at `--jobs 1 --slots 1` on the **harness9** worktree
`~/ob-ink3/.claude/worktrees/h9` at `f38a29c7` (era `036638dd`), driven from
the rig's eval-host container (`openbeast-eval-host:8d1e28442569`) by
`~/openbeast-runs/ink-I_T3-run.sh`. These notes are kept out of the public
docs. The procedure repeats `ink-up8k-integrity-2026-11-24.md`, with the
review-1124 scanner from main.

| Label | Board row | File (`evals/boards/v5/`) | Header time (UTC, naive) | End (campaign log, UTC) | Rows |
|---|---|---|---|---|---|
| `I_T3` | `INK-T3` | `eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-003039.json` (sha256 `f0f15c624baec3ed21d28485055b63fe29848d888c3d4c7ad59242683bcd3dd3`) | 2026-11-25T00:30:40 | 2026-11-25T09:13:10 | 71 |
| `I_T3` r1 | `INK-T3` (second file) | `eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-091311.json` (sha256 `f56d3466ca5351d5d60c62db1170939fa80f5e891478bc0281ecf415f73fd403`) | 2026-11-25T09:13:12 | 2026-11-25T10:18:12 | 4 |

`~/openbeast-runs/beast-inkling-I_T3-20261124-1630.out` (the campaign log,
UTC): `START I_T3` 00:30:39, `END I_T3 rc=1 52/67 scorable` 09:13:10,
`RERUN I_T3 r1: 4 units (lost to infra: server_error): 163_bigint_divmod_d,
176_intermittent_500s_a, 183_heston_little_trap, 192_pike_regex` 09:13:11,
`END rerun` 10:18:12, `DONE I_T3: 52/71 scorable`. The launcher log
(`ink-I_T3-run.out`, PST) ends `RESTORE OK` at 02:18:19 (the gate `:8091`
200 and a chat from Qwen3.8) and `I_T3 LAUNCHER DONE`. Both committed
copies hash the same as the rig's files (read 2026-11-25 ~02:25 PST). Under
Max's overflow rule the first file's four `server_error` rows are
overflows (below, and `overflow.json`), so the second file supersedes
nothing on the board; it is committed as the run's verbatim record.

## Copies

The rig was read only, except the `dlsym` replay directory (below). `tar`
over ssh to `/tmp/inkt3/rig/`: `~/ob-ink3/.claude/worktrees/h9/agents/logs/`
(75 files, `agent-20261125-003040-*` to `agent-20261125-094812-*`, every
one this arm's), both results files, both `diag-timing-*.jsonl`, the
campaign directory, the launcher script and log, the campaign log and the
server log `~/openbeast-runs/basalt-server-logs/basalt-I_T3-20261124-162910.log`
(2,239,061 bytes). Task texts: `git archive f38a29c7` to `/tmp/inkt3/h9`.

## The scan (2026-11-25 ~02:30 PST, on the Mac)

From a worktree of origin/main at `3e75da69` (the review-1124 scanner):

```bash
L=/tmp/inkt3/rig/ob-ink3/.claude/worktrees/h9/agents/logs; R=/tmp/inkt3/rig/ob-ink3/.claude/worktrees/h9/evals/results
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir $L --until I_T3=2026-11-25T10:18:12 --json /tmp/inkt3/scan.json \
    "I_T3=$R/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-003039.json,$R/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-091311.json@/tmp/inkt3/h9"
```

**75 transcripts, 71 units, 0 contaminated, exit 0.** Every hit is a
refused web call (`attempts/info only`):

| Unit | Transcript | Calls |
|---|---|---|
| 126_hmac_scratch (PASS) | `agent-20261125-005446-9ae96ca5` | it4 `web_search` (an RFC 4231 test vector), refused |
| 163_bigint_divmod_a (FAIL) | `agent-20261125-013634-8d65a6e6` | it5 `web_search` (Knuth Algorithm D), refused |
| 163_bigint_divmod_b (FAIL) | `agent-20261125-020041-185a88da` | it5, it16 `web_search`, it11 `fetch` (Wikipedia, Division algorithm), all refused |
| 163_bigint_divmod_d (the overflow row) | `agent-20261125-022332-e95b477f` | it19 `web_search`, it23 `fetch` (Wikipedia), refused |
| 163_bigint_divmod_d (r1) | `agent-20261125-091312-323dd404` | it3, it5, it29 `web_search`, refused |
| 163_bigint_divmod_f (FAIL) | `agent-20261125-025410-5e962938` | it4 `fetch` (Wikipedia), refused |
| 183_heston_little_trap (the overflow row) | `agent-20261125-071403-26ce8002` | it7 `web_search` (Albrecher et al. 2007), refused |
| 183_heston_little_trap (r1) | `agent-20261125-093846-d4b81e4e` | it6 `web_search`, refused |
| 192_pike_regex (r1) | `agent-20261125-094812-f89c29d0` | it4, it5 `web_search` (Pike VM), refused |

The review-1124 scanner also checks the shell's routes off the host
(`curl`/`wget` hosts, `git` remotes, package installs, `nc`/`ssh`/`scp`,
httpie, Python/Node socket and URL one-liners) and listings of an answer
tree in any format and under any root: no hit in any of the 75. The
`find /`-style exposure of INK-UP8K's `163_bigint_divmod_c` did not recur
(web tools were offered, as in every T2 arm).

**Attribution note.** The scanner treats the r1 transcripts as the scored
ones for the four rerun units (it follows `campaign.infra_reason`, which
still sees `server_error`); the board scores the first file's rows for
them (`overflow.json`). Both sets were scanned, so the verdict covers every
transcript either view scores.

## INK-T2-STOCK rescanned with the same scanner (a false positive, fixed)

The I_T2_stock transcripts (71, `~/ob-ink2/.claude/worktrees/h9/agents/logs/`
`agent-20261124-140740-*` to `agent-20261124-182500-*`) were copied to
compare capped turns, and scanned with the same tool (`--until
I_T2_stock=2026-11-24T18:25:17`). It flagged **1 contaminated**:
`168_linearizability_kv_e` (FAIL), `agent-20261124-164947-15b318d4`, it15,
kind `network`, evidence `nc =`, `nc ==`, `nc {`. The call is a `python3 -
<< 'PYEOF'` heredoc that rewrites the unit's own `linz.rs`; the Rust source
in the heredoc uses a variable named `nc`. `_HOSTCMD` matched `nc` at a
word start and `_first_host` returned `=` / `==` / `{` as the host, which is
not loopback, so the scanner called it network. Nothing reached off the
host (the result is `done`). **False positive.** Fixed in this PR:
`_first_host` returns a token only when it is a host name or an address
(`_HOST_TOKEN`), with a test case in `tests/test_eval_integrity_g24.py`
(the identifier forms; the existing `nc example.com 80`, `nc 127.0.0.1
9000` and `nc -l 8080` cases unchanged). Rerun after the fix: **I_T2_stock
71 transcripts, 0 contaminated**; I_T3 unchanged (0). This is the first
leg of TODO's "rescan the rig arms with the review-1124 scan" (INK-T2-STOCK
done; INK-T2, -S1, -UP8K, Q_T2, the calibration and Q38U-L0 remain).

## The overflow rows

`evals/audit/overflow_audit.py` over the first file with `--until
2026-11-25T09:13:10` (its own end) proposed `overflow` for all four. Run
over both files with the campaign's end it first matched each main row to
the r1 transcript of the same unit (the window ran past the rerun's start)
and reported `undetermined` with "the transcript's error count differs
from the row's"; the guard worked, and this PR ends each file's window at
the next file's start (`tests/test_overflow_audit.py`).

| Unit | Transcript | Replies | First error | Errors (row) | Prompt at first / last error | Room left | Retry time |
|---|---|---:|---|---:|---|---:|---|
| 163_bigint_divmod_d | `agent-20261125-022332-e95b477f` | 85 (10 at the cap) | turn 86, 02:42:02Z | 95 (95) | 261,884 / 261,913 | 252 | 8.0 of 26.5 min |
| 176_intermittent_500s_a | `agent-20261125-064104-74fb19a1` | 242 (0 at the cap) | turn 243, 06:54:30Z | 58 (58) | 245,865 / 245,894 | 16,271 | 4.9 of 18.3 min |
| 183_heston_little_trap | `agent-20261125-071403-26ce8002` | 106 (1) | turn 107, 07:28:38Z | 74 (74) | 247,259 / 247,288 | 14,877 | 6.2 of 20.8 min |
| 192_pike_regex | `agent-20261125-081135-d1eac0aa` | 68 (1) | turn 69, 08:25:47Z | 112 (112) | 249,367 / 249,396 | 12,769 | 9.4 of 23.6 min |

Every error is Basalt's `Error code: 400 - request exceeds the context
window: prompt (N tokens) + max tokens (16384) exceeds the context
(262144); requests are never truncated. Send a smaller max_tokens (at most
R here), or start the server with --fit-max-tokens …`. The server log has
389 `engine reported request exceeds the context window` lines in the arm's
window, each followed by `s1 done pp=N/0c tg=0 end=error`; 339 of them align
with these four transcripts' errors to within 1 ms, and the other **50** with
`168_linearizability_kv_c` (`agent-20261125-042131-ff195887`): 48 replies,
the first overflow at turn 49 (04:47:23Z, prompt 251,439), then retries
until the wall (1,800 s; `agent_exit_code -1`, `stats_partial`, the row's
`api_errors` 0 because the partial-stats sidecar does not carry them). That
row was already a counted fail (a wall kill), so it needs no ledger entry;
the board note and the report name it. 163_bigint_divmod_d's last reply
before the overflow was itself a full 16,384-token reply at a 245,429-token
prompt (decode 311 tok/s there). 0 compactions in all five: the runner's
`_CTX_OVERFLOW_RE` does not match Basalt's wording (HARNESS_NOTES,
"Next-era: context overflow is a model outcome", proposal (a)).

The four r1 transcripts (`091312-323dd404`, `092644-34c6b7bc`,
`093846-d4b81e4e`, `094812-f89c29d0`) have 0 API errors; all four failed on
a verdict (row `validation_output`: 163_d `CPU budget`, 176_a a hidden test,
183 a crash, 192 a missing function). Not board inputs under the rule.

## 122_gemm_blocked_d: the dlsym check

The row reads `FAIL: the multiply must be your own code: the binary imports
dlsym` (the Linux-host validator bug). Replayed as PR #303, #312, #313 and
the I_UP8k review:

- **Rebuild** (`agent-20261125-004042-fb1b1a77.jsonl`, copied to
  `tx/t3.jsonl`). Three calls wrote `gemm.rs`: line 29 (a quoted heredoc
  `cat > /tmp/eval_gemm/gemm.rs << 'EOF'`, then `rustc`), line 81
  (`edit_file`; line 78 was the same call refused for a missing `path`),
  line 84 (`sed -i` on one line, then `rustc` and a sample run). Lines 74
  and 99 are `read_file` results of the whole file (kept to 2,000
  characters: compared as the first 65 / 66 lines, the last a prefix); both
  match. Any other call that wrote the file stops the rebuild; none did.
  Final: 2,955 characters, sha256
  `838be7adca1b4d2282d4168ca20550d063007abd54ea7b313036136aaa280aba`.
- **Replay directory on the rig:** `~/ob-cal/.claude/gemm-replay-ink-t3/`
  (new; the earlier four untouched): `rebuild.py`, `tx/`, and `replay.py`,
  a copy of `gemm-replay-ink-up8k/replay.py` whose code from `import json`
  down is byte-identical (only the docstring differs). Each step in a new
  `--rm` eval-host container from `~/ob-cal` (h9 worktree at `f38a29c7`,
  this arm's tree), `nice -n 19`, `OPENBEAST_EVAL_CONTAINER_CPUS=4`; the
  rig's default stack (Qwen3.8) untouched:

  ```bash
  OPENBEAST_EVAL_CONTAINER_CPUS=4 nice -n 19 scripts/eval-host-container.sh run -- \
      nice -n 19 python3 .claude/gemm-replay-ink-t3/replay.py rebuild
  OPENBEAST_EVAL_CONTAINER_CPUS=4 nice -n 19 scripts/eval-host-container.sh run -- \
      nice -n 19 python3 .claude/gemm-replay-ink-t3/replay.py grade t3 --original   # then without, x3
  ```

  Rebuild 10:31:13Z; grading 10:31:14 to 10:31:29Z, timeout scale 2.0.
- **Control, the unpatched validator:** `VERDICT t3 [original rule]: FAIL`,
  `the binary imports dlsym`, as recorded.
- **Proposed rule:** `REPLAY-RULE: allowed dlsym (Rust ELF; no other loader
  or BLAS import)`, `OK: 6 hidden cases`, **PASS in 3 of 3 runs**. The
  patched validator written is byte-identical to the earlier replays'
  (`validate_d_proposed.py`, sha256 `db11379935ae7177…`).

`exclusions.json` gets INK-T2's `validator_notes` wording for `INK-T3`.
Not adjusted: under the proposed rule INK-T3 would be 53/71.

## Capped turns and the server log

`evals/audit/capped_turns.py --cap 16384` over a copy of the scan report
with the four overflow units attributed to their first-file transcripts
(the board's view; `scan-board.json`): 71 units, 3,596 turns (3,204
replies; the rest are the 339 + 50 overflow retries and the wall-killed
turns), **136 ended at the cap: 0 empty, 93 text only, 43 still carrying a
call**. Over the scanner's own attribution (the r1 transcripts): 133 at the
cap, 0 / 99 / 34. Over all 75 transcripts: 101 text-only capped replies
(content median 23,248 characters, p90 57,597) and 44 with a call (content
median 134). INK-T2-STOCK (same tool, `--cap` from its events, 8,192): 190
at the cap, 105 empty, 2 text only, 83 with a call.

`evals/audit/basalt_log.py … --cap 16384` (UTC windows from the campaign
log): the main file, `--since 2026-11-25T00:30:39 --until
2026-11-25T09:13:10`: 3,205 requests (3,068 `stop`, 136 `length`, 1
`cancelled`), **1 "no answer" warning**, generated 8,484,542 and prompt
203,158,812 tokens (the rows: 8,478,549 and 203,079,166; the 8 wall-killed
rows carry partial stats), decode median 427.9 tok/s on replies of 256+
tokens, 503.4 at the cap, drafts accepted 91.6% (token-weighted), cached
share 90.9%, 0 requests without a budget line. The whole campaign window
to 10:18:12: 3,851 requests, 145 `length`, 2 `cancelled`, 1 "no answer".
The one warning (04:09:13Z) is `168_linearizability_kv_b`'s turn 21
(`agent-20261125-035939-5870dd54`): `end=length` at 16,384 after the
wrap at 8,192; the transcript records a text reply (an `assistant` event
with content), no call. So the client got text, not an empty reply.

## Everything else

No other read-only look outside `/tmp` was flagged. The four `I_T3`
gate probes before the arm (`ink-I_T3-run.out`, 16:29:21-16:30:37 PST):
4 of 4 replies made a call, 0 empty.
