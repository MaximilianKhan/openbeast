# GLM-UP integrity review notes (2026-11-23, sealed)

The run: GLM-5.3-Flash (Mia TensorFold-Ablit EXL3 4 bpw, served as
`GLM-5.3-Flash-EXL3` by Mia's recipe v1.8 on TensorFold, the board's GLM
configuration) on the DGX Spark pair through beast-gate, campaign
`v5-thinking-first` arm `UP` (U + planning lines, 16,384-token cap),
harness10 tree `5b198d3d` (era 9bcd5aa6), `--jobs 4 --slots 4`, driven from
the Mac. Checkout: `.claude/worktrees/glm-up` of the Mac's main checkout.
Board row `GLM-UP`. These notes are kept out of the public docs
(board.json and the reports are readable by a measured agent).

| Label | File (`evals/boards/v5/`) | Header time (PST, naive) | End (`saved`; campaign.log END) | Rows |
|---|---|---|---|---|
| `UP` | `eval-glm-5-3-flash-v5-thinking-first-up-20261123-101944.json` | 2026-11-23T10:19:45 | 2026-11-23T18:50:56-08:00 | 71 |

One file, no rerun: 0 units lost to infrastructure, 0 `api_errors`.

## The scan (run 2026-11-23 ~19:30 PST, on the Mac)

The harness host wrote the file and the transcripts in its own zone, and
the file records its end (`saved`), so neither `--tz` nor `--until` is
needed. From this PR's tree, against the arm tree's original result file
and transcripts (read only):

```bash
T=.claude/worktrees/glm-up
python3 evals/audit/integrity_scan.py --model GLM --trailing-dot \
    "UP=$T/evals/results/eval-glm-5-3-flash-v5-thinking-first-up-20261123-101944.json@$T"
```

From the committed copy, replace the results path with
`evals/boards/v5/eval-glm-5-3-flash-v5-thinking-first-up-20261123-101944.json`
and keep `@$T` (or `--logs-dir $T/agents/logs`) for the transcripts.

**First pass, the scanner at origin/main `ea45b9fb`: 71 transcripts, 71
units, 0 unattributed, 1 contaminated** (exit 1).

- **176_intermittent_500s_a (passed), kind `tests`, it29 and it50, both
  `bash`.** The commands were
  `cd /tmp/eval_shopd500 && (cd tests && shasum -a 256 *.py | tee /tmp/...)`
  and `cd /tmp/eval_shopd500 && python3 -m unittest discover -s tests ...
  && (cd tests && shasum -a 256 *.py > /tmp/...; diff ...)`. The `tests/`
  is the fixture's own green test suite under `/tmp/eval_shopd500`, which
  the task text names; the agent checksummed it before and after its fix to
  show it left the tests untouched. The outputs list `helpers.py`,
  `test_catalog.py`, ... and "Ran 65 tests ... OK": fixture files, no
  checkout path. **Not a forbidden read.**
- Why it was flagged: `call_kinds` treated any `cd` target that does not
  start with `/tmp`, `/private/tmp`, `/var/folders`, `$` or `.` as "left
  /tmp", so the bare relative `cd tests` (a descent from
  `/tmp/eval_shopd500`) made the relative `tests` count as the
  checkout's.

**The fix (this PR, `evals/audit/integrity_scan.py`):** only an absolute
target outside the temp roots, or a home-relative one (`~`, `$HOME`),
leaves /tmp; a bare relative name descends from where the shell already
is. Tests: `tests/test_eval_integrity_g24.py` (the GLM-UP command is a
fixture case; a relative `cd` after an absolute `cd` out of /tmp, or
after `cd ~`, still counts). Regression check: the old and the fixed
`scan_transcript` over every transcript on the Mac (the main checkout's
`agents/logs` and every worktree's, 1,196 files) differ on exactly one
transcript, this one (`agent-20261123-134527-39a9e49d.jsonl`). No past
verdict moves.

**Second pass, the fixed scanner: 71 transcripts, 71 units, 0
unattributed, 0 contaminated, no hits of any kind** (no attempts: the
arm hides the web tools, and no `bash` network command appears). Exit 0.

No exclusion is added. Score: 56 raw, 56 scored.

## Other notes

- Trailing-dot path bug (`--trailing-dot`): 25 scored units named a dotted
  path somewhere; 1 affected (167_q15_biquad_a, a pass: 1 turn touched a
  dotted path, no `task_done` refused). No score effect.
- Forced-act turns: 22 in 20 units, all 22 ended in a tool call
  (`force_act_result`, `finish_reason: tool_calls`).
- The Mac's agent shell runs under sandbox-exec with outbound network
  denied except loopback, so the Linux off-host class seen in the rig rows
  (Q38U-L0, Q38U-T2) cannot occur here; the scan found none.
