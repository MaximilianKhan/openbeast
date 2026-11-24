# DS-T2 eval-host calibration integrity review notes (2026-11-23, sealed)

The runs: DeepSeek-V4.1-Flash-1017 arm T2, rerun from the RTX 5090 rig's
eval-host container (`openbeast-eval-host:8d1e28442569`, Linux) on the
harness9 tree `f38a29c7` (era 036638dd), checkout
`~/ob-cal/.claude/worktrees/h9` on the rig. Diagnostics only, not board
rows. Result files in `evals/boards/v5/diagnostics/`:

| Label | File | Header time (UTC, naive) | Rows |
|---|---|---|---|
| `CAL` | `eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json` | 2026-11-22T23:03:35 | 71 |
| `R1` | `eval-deepseek-v4-1-flash-v5-thinking-first-t2-rerun-20261123-051155.json` | 2026-11-23T05:11:56 | 7 |
| `R2` | `eval-deepseek-v4-1-flash-v5-thinking-first-t2-redo-fails-20261123-071205.json` | 2026-11-23T07:12:05 | 11 |

These notes are kept out of the public docs (board.json and the reports are
readable by a measured agent).

## The scan that was run (2026-11-23, on the rig)

The calibration report (§4) records: `evals/audit/integrity_scan.py` on the
rig over all three files, `--tz UTC`, with the files' original mtimes as
the run ends (no `--until`). Result: 71 + 7 + 11 transcripts, 0
contaminated; the only hits were refused `web_search` / `fetch` calls (the
scan's `web` kind, attempts, not reads). **The exact command line of that
scan was not recorded.** Its result rests on the report's account.

## The command for a rescan (from the committed files)

The committed copies have no `saved` field and git resets their mtime, so a
rescan needs `--until` per label. The ends:

- `CAL`: **2026-11-23T05:11:56**, the header time of `R1`. The three passes
  ran in sequence from one script chain, so `R1`'s start bounds `CAL`'s end
  (the run log, report §3, gives 21:11 PST = 05:11 UTC).
- `R1`: **2026-11-23T07:12:05**, the header time of `R2`, by the same
  reasoning (run log: 23:12 PST = 07:12 UTC).
- `R2`: **unknown from the committed files.** Nothing follows it. The run
  log (report §3) gives 03:57 PST = **11:57 UTC**; the file itself only
  bounds it below (its header time plus its longest unit, 3,600 s: 08:12
  UTC). Use 11:57 and say it comes from the run log.

```bash
D=evals/boards/v5/diagnostics; T=<checkout at f38a29c7>   # on the rig: ~/ob-cal/.claude/worktrees/h9
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir <the rig's ~/ob-cal/.claude/worktrees/h9/agents/logs> \
    --until CAL=2026-11-23T05:11:56 --until R1=2026-11-23T07:12:05 --until R2=2026-11-23T11:57:00 \
    "CAL=$D/eval-deepseek-v4-1-flash-v5-thinking-first-t2-20261122-230335.json@$T" \
    "R1=$D/eval-deepseek-v4-1-flash-v5-thinking-first-t2-rerun-20261123-051155.json@$T" \
    "R2=$D/eval-deepseek-v4-1-flash-v5-thinking-first-t2-redo-fails-20261123-071205.json@$T"
```

`--tz UTC`: the container writes naive UTC timestamps. The transcripts are
on the rig only. This rescan has **not** been run (it needs the rig's logs,
and the rig is running Q_T2): pending in docs/TODO.md. Expected result:
the same 71 + 7 + 11 transcripts attributed, 0 contaminated; any
difference is a finding, not a formality.
