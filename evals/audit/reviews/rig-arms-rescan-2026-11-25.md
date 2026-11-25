# Rig arms rescan with the review-1124 integrity scanner (2026-11-25, sealed)

Every v5 run that executed on the RTX 5090 rig (`beast`), rescanned with the
widened scanner (review 1124, PR #318; the `nc`-as-variable fix of #326), as
the TODO item "Rescan the rig arms with the review-1124 integrity scan"
asks. Max's go: 2026-11-25. These notes are kept out of the public docs
(board.json and the reports are readable by a measured agent); the public
record is DECISIONS-2026-11-25 §5 (counts and verdicts only).

## Scope and copies

The rig was read only. A long job (SC117 conversion, then `I_T2_sc117`)
was running there: nothing under `~/Documents/openbeast`, no container, no
GPU, nothing under `~/openbeast-runs/sc117*` was touched. One `nice -n 19
tar` over ssh copied, to `/tmp/rigscan/rig/` on the Mac:

| Rig path | Transcripts | First / last |
|---|---:|---|
| `~/ob-eval-container/agents/logs` (tree 86f07847) | 123 | `agent-20261122-083815-351954df` / `agent-20261122-225936-48eb229b` |
| `~/ob-q38/.claude/worktrees/h9/agents/logs` (f38a29c7) | 78 | `agent-20261123-115750-fb46702f` / `agent-20261124-010046-c1100edd` |
| `~/ob-cal/.claude/worktrees/h9/agents/logs` (f38a29c7) | 89 | `agent-20261122-230336-047e6b7b` / `agent-20261123-105735-c33bbc02` |
| `~/ob-ink/.claude/worktrees/h9/agents/logs` (f38a29c7) | 71 | `agent-20261124-045843-9ee241e7` / `agent-20261124-092926-bcb98123` |
| `~/ob-ink2/.claude/worktrees/h9/agents/logs` (f38a29c7) | 142 | `agent-20261124-095657-7a4caaca` / `agent-20261124-182500-3b4e89fe` |
| `~/ob-ink2/agents/logs` (tree d41c2c47, harness10) | 71 | `agent-20261124-182530-c60b64df` / `agent-20261124-222755-01762698` |
| `~/ob-ink3/.claude/worktrees/h9/agents/logs` (f38a29c7) | 75 | `agent-20261125-003040-7ba31418` / `agent-20261125-094812-f89c29d0` |

plus each tree's `evals/results/`. Every rig results file is byte-identical
to its committed copy in `evals/boards/v5/` or `diagnostics/` (cmp), and the
copies kept their mtimes (tar), which match the `--until` values below.
Task texts: `git archive` of f38a29c7, 86f07847 and d41c2c47 to
`/tmp/rigscan/trees/`.

## The commands (2026-11-25, on the Mac, from origin/main at 8e85ee39)

`--tz UTC` everywhere (the container writes naive UTC). `--until` is each
file's end: the campaign-log END lines used by the earlier reviews, which
equal the rig files' mtimes (L0: its mtime 16:38:11Z; CAL R2: its mtime
11:57:46Z, which the cal review only knew as "11:57 from the run log").

```bash
R=/tmp/rigscan/rig; B=evals/boards/v5; D=$B/diagnostics
T9=/tmp/rigscan/trees/f38a29c7; T10=/tmp/rigscan/trees/d41c2c47; TC=/tmp/rigscan/trees/86f07847
S="python3 evals/audit/integrity_scan.py --tz UTC"
$S --logs-dir $R/ob-eval-container/agents/logs --until Q38U_L0=2026-11-22T16:38:11 "Q38U_L0=$B/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L0-20261122-083815.json@$TC"
$S --logs-dir $R/ob-eval-container/agents/logs --until 2026-11-22T23:03:00 \
   "Q38U_L1=$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L1-20261122-192701.json,$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L1-20261122-163812.json@$TC" \
   "Q38U_L2=$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-192701.json,$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-163812.json@$TC"
$S --logs-dir $R/ob-q38/.claude/worktrees/h9/agents/logs --until Q_T2=2026-11-23T23:11:11 --until R1=2026-11-24T01:26:32 \
   "Q_T2=$B/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json@$T9" \
   "R1=$B/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-231112.json@$T9"
$S --logs-dir $R/ob-cal/.claude/worktrees/h9/agents/logs --until CAL=2026-11-23T05:11:56 --until R1=2026-11-23T07:12:05 --until R2=2026-11-23T11:57:46 \
   "CAL=$D/eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json@$T9" \
   "R1=$D/eval-deepseek-v4-1-flash-v5-thinking-first-t2-rerun-20261123-051155.json@$T9" \
   "R2=$D/eval-deepseek-v4-1-flash-v5-thinking-first-t2-redo-fails-20261123-071205.json@$T9"
$S --logs-dir $R/ob-ink/.claude/worktrees/h9/agents/logs --until I_T2=2026-11-24T09:29:33 "I_T2=$B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json@$T9"
$S --logs-dir $R/ob-ink2/.claude/worktrees/h9/agents/logs --until I_T2_s1=2026-11-24T14:07:28 "I_T2_s1=$B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-s1-20261124-095656.json@$T9"
$S --logs-dir $R/ob-ink2/.claude/worktrees/h9/agents/logs --until I_T2_stock=2026-11-24T18:25:17 "I_T2_stock=$B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json@$T9"
$S --logs-dir $R/ob-ink2/agents/logs --until I_UP8k=2026-11-24T22:28:05 "I_UP8k=$B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-up8k-20261124-182529.json@$T10"
$S --logs-dir $R/ob-ink3/.claude/worktrees/h9/agents/logs --until I_T3=2026-11-25T10:18:12 \
   "I_T3=$B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-003039.json,$B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t3-20261125-091311.json@$T9"
# and a whole-directory pass over each of the seven log dirs (ALL=@$T9), so
# no transcript outside a results window goes unscanned
```

Every scan attributed every result row (0 unattributed). Windows overlap by
the scanner's 120 s backward slack, so a few transcripts are scanned under
two labels (I_T2_stock's 81 include I_T2_s1's last 10 quick units; Q_T2 R1's
10 include Q_T2's last 3); each is still judged once on its own content.
The whole-directory passes found nothing the per-arm scans did not.

## Results (after the scanner fixes below)

| Row / run | Label(s) | Transcripts | Units | Contaminated | Refused web calls (transcripts) | Other attempts |
|---|---|---:|---:|---|---:|---|
| Q38U-L0 | Q38U_L0 | 71 | 71 | 3: 126_hmac_scratch (PASS), 163_bigint_divmod_e, 192_pike_regex (already excluded) | 7 (5) | 122_gemm_blocked_b it21 (`pip3` not found) |
| Q38U-T2 | Q_T2 + R1 | 71 + 10 | 71 + 10 | 6: 122_gemm_blocked_d (r1), 124_rkf45 (PASS), 126_hmac_scratch (PASS) (already excluded); **123_nbody_b (PASS), 168_linearizability_kv_c (FAIL) (new)** | 6 (6) | 122_gemm_blocked_a it8 (`pip3` not found) |
| INK-T2 | I_T2 | 71 | 71 | 0 | 5 (4) | none |
| INK-T2-S1 | I_T2_s1 | 71 | 71 | 0 | 6 (4) | none |
| INK-T2-STOCK | I_T2_stock | 81 | 71 | 0 (confirms the I_T3 review's rescan) | 5 (5) | none |
| INK-UP8K | I_UP8k | 71 | 71 | 1: 163_bigint_divmod_c (already excluded; the `find /` listing, reads refused) | 0 (web not offered) | the two refused `evals/refs` reads of that unit |
| INK-T3 | I_T3 (both files) | 75 | 71 | 0 (confirms `ink-t3-integrity-2026-11-25.md`) | 15 (9) | none |
| diag: DS T2 calibration | CAL, R1, R2 | 71 + 7 + 11 | 71 + 7 + 11 | 0 | 5 (5) | none |
| diag: Q38U profile L1/L2 | Q38U_L1, Q38U_L2 | 52 (shared window) | 39 | 0 | 7 (4) | none |

Every `web` hit was the eval guard's refusal (`Error: fetch is disabled ...`
/ `web_search`), an attempt by the rule.

## Triage of every non-web hit

**Q38U-T2 `123_nbody_b` (PASS), transcript `agent-20261123-125347-df91e726`
(sha256 prefix `ec1583bb80c04a7d`): CONTAMINATED, real off-host access.**
After an import of `mpmath` failed (it17), the agent tried `pip install`
(it18: `pip: command not found`), `python3 -m pip install mpmath` (it19:
pip downloaded, then `OSError: [Errno 13] Permission denied` writing the
read-only eval venv), and `python3 -m pip install --target=<a /tmp dir>
mpmath` (it20: `Successfully installed mpmath-1.4.1`). The eval-host image
sets `PIP_NO_CACHE_DIR=1` and no index (evals/host/Containerfile), and the
container runs `--network host`, so the package came from the public index:
the host was reached. The agent then used the library to compute a
high-precision reference trajectory for its own checks (it20, it24, it41).
The package is a general maths library, not an answer key; by the scan's
rule (and the precedent of the RFC test-vector fetches excluded on
2026-11-22/23) an off-host read is contamination. The 2026-11-23 scan did
not see it: package installs were not network until review 1124.
**Excluded; the unit was a pass: Q38U-T2 52 → 51 scored.**

**Q38U-T2 `168_linearizability_kv_c` (FAIL), `agent-20261123-182408-efa5af6a`
(`c2344435181a1fe7`): CONTAMINATED by the rule, almost certainly nothing
fetched.** it60: `command -v gdb || (apt-get install -y gdb >/dev/null 2>&1
&& command -v gdb) || echo NOGDB`; the result is `NOGDB`, the install's
output discarded. The container runs as the unprivileged user `eval`
(Containerfile `USER eval`) with `--cap-drop ALL --security-opt
no-new-privileges` (scripts/eval-host-container.sh), and the image deletes
`/var/lib/apt/lists/*`: `apt-get install` fails on the dpkg lock (or on an
empty package list) before any download, and the venv write in
123_nbody_b proves the uid was not root. But the transcript itself cannot
show it, and Q38U-L0's `163_bigint_divmod_e` (a `curl` whose arrival could
not be told) set the precedent: treated as a read. **Excluded; a fail, no
score effect.** The scanner was not changed for this: it would have to
trust facts about the host the transcript does not carry.

**Q38U-T2 `122_gemm_blocked_a` (PASS), `agent-20261123-120025-77473e1e`
(`d6d5afbcdc1b33ff`): FALSE POSITIVE, fixed.** it8 `pip3 install --quiet
numpy 2>&1 | tail -1; ...`, result `/bin/bash: line 1: pip3: command not
found` and the agent's own `NO_NUMPY`: the command does not exist in the
image, nothing ran. The scanner flagged it `read network`. Fixed (below);
now an attempt. Verdict stands.

**Q38U-L0 `122_gemm_blocked_b` (PASS), `agent-20261122-085103-4d46a0e9`
it21: attempt, for the wrong reason before the fix.** The same `pip3: command
not found`. The scanner already called it refused, but only because its
failed-connection regex, case-insensitive, matched `ENOTFOUND` inside
`ModuleNotFoundError` in the same result. With the fix it is an attempt by
the `command not found` rule. Verdict stands.

**Already excluded, re-confirmed by reading the calls:** Q38U-L0
126_hmac_scratch it5/it6/it8, 163_bigint_divmod_e it7, 192_pike_regex it3
(`curl` to public hosts); Q38U-T2 124_rkf45 it14/15/17, 126_hmac_scratch
it5/9/10 (`curl`), 122_gemm_blocked_d r1 it44 (`curl` of a compiler source
file); INK-UP8K 163_bigint_divmod_c it8 (the listing; it9/it10 refused).
No new kind of hit (answer-tree listings in other formats, `ssh`/`nc`/
`scp`, socket one-liners, `git` remotes) appeared in any rig transcript.

## Scanner fixes (this PR)

1. **`command not found` is an attempt** (`_never_ran`): a bash call whose
   every network command is reported by the shell as `<cmd>: command not
   found` / `not found`. A command not named in such a line keeps the hit
   (a missing `pip3` beside a `curl` that ran is still a read; a missing
   `gdb` beside a `pip install` that succeeded is still a read). Test
   `test_scan_missing_network_command_is_an_attempt`.
2. **`ENOTFOUND`/`EAI_AGAIN`/`ECONNREFUSED` matched case-sensitively as
   whole words.** Case-folded, `ENOTFOUND` matched every Python
   `ModuleNotFoundError`/`FileNotFoundError`, so a real `curl` that printed a
   traceback in the same call would have been downgraded to an attempt.
   This strengthens detection. In the rig arms it changed no verdict (the
   only call it touched was the L0 `pip3` above). Test
   `test_scan_a_python_traceback_is_not_a_failed_connection`.

Follow-up (not done here): `_NET_FAIL`'s `failed: ` alternative is just as
loose (any `N failed: ...` test summary next to a fetch would downgrade
it); no rig hit depended on it. Worth tightening to wget's forms with a
rescan of the Spark arms.

## Board effect

`exclusions.json`: two entries for run `Q38U-T2` (123_nbody_b,
168_linearizability_kv_c). `v5_board.py --write`: **Q38U-T2 52/71 → 51/71,
SCORE 75.6 → 75.0** (54 raw; SOLVE 72.3 unchanged; LANG 85.6 → 83.1, one
language 7/8 → 6/8), still #6. The paired comparisons against Q38U-T2 keep
their gained/lost and p (both units have the same verdict in every Inkling
row), over 63 clean units instead of 65; L0 → Q_T2 over 59 instead of 61,
+12/−1, p 0.003. Every other rig row and diagnostic is unchanged.
