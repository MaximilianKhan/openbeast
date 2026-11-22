# Q38U profile L1/L2 integrity review notes (2026-11-22, sealed)

The runs: Qwen3.8 27B Uncensored MTP Q5_K_M, the profile's L1 (packs) and
L2 (packs + diagnostics + escalation) arms, RTX 5090, llama.cpp, harness10
(era 9bcd5aa6), driven from the rig's eval-host container (Linux), tree
`~/ob-eval-container` @ 86f07847. Diagnostics only, not board rows. Result
files in `evals/boards/v5/diagnostics/`:

- final pass (the `--resume`, started 19:27 UTC):
  `eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L1-20261122-192701.json`,
  `eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-192701.json`;
- the interrupted first pass (16:38 to about 19:26 UTC), kept because the
  final files replay rows from it (`from_cache`: 2 in L1, 17 in L2):
  `eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L1-20261122-163812.json`,
  `eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-163812.json`.

These notes are kept out of the public docs (board.json and the reports are
readable by a measured agent).

## The scan

Run on the Mac against copies of the rig's files (`scp -p`, so the mtimes
were kept), with the transcripts copied from the rig's
`~/ob-eval-container/agents/logs` and TREE a detached checkout at 86f07847:

```bash
D=evals/boards/v5/diagnostics; T=<checkout at 86f07847>
python3 evals/audit/integrity_scan.py --tz UTC --logs-dir <copy of the rig's agents/logs> \
    --until 2026-11-22T23:03:00 \
    "Q38U_L1=$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L1-20261122-192701.json,$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L1-20261122-163812.json@$T" \
    "Q38U_L2=$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-192701.json,$D/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-L2-20261122-163812.json@$T"
```

`--tz UTC`: the container writes naive UTC timestamps. `--until`: the
committed copies have no `saved` field and git resets their mtime; the
final pass's last write was 23:02:36 UTC (15:02:36 PST), and no later
transcript exists in the logs. Both first-pass files must be listed: the 19
replayed rows' transcripts lie in the first pass's window, before the final
files' header time. Without `--tz`/`--until` the scan attributes 0 of 39
L2 rows and exits 2 (a partial scan, not a verdict).

Result (exit 0): L1 52 transcripts, L2 52 transcripts (the same set: both
arms ran interleaved in one process, so their windows coincide), 39 units,
every result row attributed, **0 contaminated transcripts. No unit is
withheld.** The run with the kept mtimes and no `--until` gave the same
verdict (51 transcripts in L2's window, 0 contaminated).

## Attribution caveat (L1 and L2 share one window)

L1's 6 zig units are also in L2, with the same task text. The scan picks
the latest matching transcript per unit, so for those 6 it attributes the
L2 transcript to both arms. This does not affect the verdict: every
transcript in the window is scanned whatever its attribution. By order and
elapsed time (L1 runs first for a unit, then L2) the pairs are, e.g.,
36_black_scholes_f: L1 = agent-20261122-225426-2be15598 (300.7 s),
L2 = agent-20261122-225936-48eb229b (171.0 s).

## Hits (all attempts, none contaminated)

Only refused web tools; no `bash` network access (curl/wget/git) in any
transcript of these arms, unlike L0:

- **36_black_scholes_f, L1 (passed).** One refused `fetch` of a numerical
  library's erfc source on raw.githubusercontent.com (not this project's
  repository).
- **36_black_scholes_c, L2 (passed).** One refused `web_search` for the
  normal-CDF approximation the task text itself names.
- **163_bigint_divmod_e, L2 (failed).** One refused `web_search` for the
  algorithm's pseudocode, in the first pass's attempt
  (agent-20261122-185613-e269844a), a wall-hit fail that was not replayed;
  the scored row is the final pass's rerun, which had no hits.
- **163_bigint_divmod_f, L1 (failed).** Two refused `web_search` and two
  refused `fetch` calls (a course page, a GitHub-hosted copy of the
  algorithm text), in the first pass's attempt (agent-20261122-191113-01cb4a99),
  likewise superseded by the final pass's rerun (no hits).

## server_error and the 500 loop in these arms

- First pass: L2 122_gemm_blocked_b (21 HTTP 500 retries) and
  163_bigint_divmod_d (2) were recorded `server_error`, not cached, and
  rerun in the final pass (both failed there, with no 500s).
- Final pass: L2 163_bigint_divmod_c (agent-20261122-195049-67810461) hit
  the same loop, 11 retries of "Failed to parse tool call arguments as
  JSON" from 20:04:55 UTC, until the wall; it is recorded as a wall-hit fail
  (`api_errors` 0, `stats_partial`), not `server_error`. It failed in L0
  too, so no contrast moves.
- The final L1 and L2 files have no `server_error` row. The ablation's
  dropped pairs are L0's four `server_error` units (two of them,
  122_gemm_blocked_c and _d, are in L2: c passed, d failed).

## Follow-up

- Same as the L0 notes: HARNESS_NOTES next-era (a) (a truncated tool call
  is a model turn) and (d) (Linux shell network deny; zone in the header).
- Scan: when two arms run interleaved, attribute by start time and elapsed
  seconds, or record the arm in the transcript's start event.
