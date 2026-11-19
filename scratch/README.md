# scratch/ — campaign tooling and the records it produced

Tracked on purpose: these scripts produced the A/B verdicts and rows
recorded in the research journals (`research/langaware/`,
`research/lowrank/`), and they are the record of how those runs were made.
They are rig-specific (absolute paths, the RTX 5090 box's models) and are
not part of the product — nothing under `scripts/` or `agents/` imports
them.

The campaign these scripts ran is over: the langaware program closed on
2026-09-30 and beast-rank was written up on 2026-10-09
([`research/FINDINGS.md`](../research/FINDINGS.md); every research note,
with its status: [`research/README.md`](../research/README.md)). Nothing
here is running or scheduled.

| File | What it is |
|---|---|
| `campaign_master3.sh` | The last campaign master (2026-09-17): Tier-3 → greedy floor → stage F (IQ2), under `gpu-lease.sh`. Tier-3 finished; the greedy floor and stage F did not (below) |
| `tier3_zig_ab.sh` · `tier3_verdict.py` | The pre-registered zig mini-A/B and its verdict |
| `tier3_cells-*.txt` | Cell manifests: `-20260915` (begun, then handed back on 09-15), `-20260917` (the six cells of the 09-17 verdict), `-stageD`, `-fresh-20260929` (the fresh rerun) |
| `tier3-verdict.txt` | The 2026-09-17 SHIP verdict (net +13). Did not survive the re-audit |
| `tier3-verdict-reaudit-2026-09-29.txt` | The re-audit of 09-17 on clean rows: UNRESOLVED |
| `tier3-verdict-fresh-20260930.txt` | The fresh rerun, 2026-09-30: SHIP (net +24), with its dated correction. This is the verdict of record |
| `greedy_floor.sh` · `greedy-floor-verdict.txt` | The churn-floor calibration (two untreated greedy rows). The verdict file records that fewer than two full rows banked: no floor was measured |
| `e32_capability3.sh` · `e32_refine.sh` · `patchup_tripwires.sh` · `row_validity.py` | E32 (`research/lowrank/experiments/32-*`): stage F (the IQ2 pair, started, not completed), our arm's correction adapters, and the row guards |
| `patchup_136.sh` · `patchup_replace.py` | Rerun and replace the one row a review subagent's `pkill` destroyed (2026-09-14) |
| `iq_stability_repro.sh` · `iq_load.py` | The IQ-artifact CUDA launch-timeout repro and its load generator (`research/lowrank/experiments/32-*/iq-stability-repro.md`) |
| `ab_verdict.py` · `run_verdict.sh` · `watch_rescues.py` · `merge_minis.py` | Verdict and analysis helpers |
| `verdict-*.json` · `b0_*_zig.txt` | Earlier A/B records (Phase A′/B) |
| `flashnext_bench.sh` | One-shot decode and prefill bench of the Flash-Next model on `:8080` |
| `gpu_handback.sh` | Wait for a cell to bank, then confirm the card is free |
| `prune-2026-09-17.sh` | The disk prune Max approved (dry run by default) |
| `spare-memory-meta.html` | The hand-built page that predates beast-artifact (its viewer test fixture) |
| `archive/` | Superseded masters and chains (`campaign_master*.sh`, `ab-*.sh`, `e32_capability*.sh`, `post_e32*.sh`, the orchestrator the research journal cites as `scratch/post_e32.sh`), kept for provenance. The pre-swap A0 cache rows (`ab-quarantine-preswap/`) moved to the sealed `evals/audit/archive-ab-quarantine-preswap/` on 2026-11-19: they hold v5 checker details |

Runtime logs go to `scratch/logs/<campaign>/` and are gitignored.
