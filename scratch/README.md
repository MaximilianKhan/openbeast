# scratch/ — campaign tooling and the records it produced

Tracked on purpose: these scripts produced the numbers in `docs/RESULTS.md`
and the research journal, and they are how a paused campaign resumes. They
are rig-specific (absolute paths, this box's models) and are not part of the
product — nothing under `scripts/` or `agents/` imports them.

| File | What it is |
|---|---|
| `campaign_master3.sh` | The current campaign: Tier-3 → greedy floor → IQ2, under `gpu-lease.sh` |
| `tier3_zig_ab.sh` · `tier3_verdict.py` | The pre-registered zig mini-A/B and its verdict |
| `tier3_cells-*.txt` · `tier3-verdict.txt` | Cell manifests and the 2026-09-17 SHIP verdict |
| `greedy_floor.sh` · `greedy-floor-verdict.txt` | The churn-floor calibration (two untreated rows) |
| `e32_capability3.sh` · `patchup_tripwires.sh` · `row_validity.py` | Stage F (the IQ2 pair) and its guards |
| `ab_verdict.py` · `run_verdict.sh` · `watch_rescues.py` · `merge_minis.py` | Verdict and analysis helpers |
| `verdict-*.json` · `b0_*_zig.txt` | Earlier A/B records (Phase A′/B) |
| `gpu_handback.sh` | Wait for a cell to bank, then confirm the card is free |
| `prune-2026-09-17.sh` | The disk prune Max approved (dry run by default) |
| `spare-memory-meta.html` | The hand-built page that predates beast-artifact (its viewer test fixture) |
| `archive/` | Superseded masters and chains, kept for provenance |

Runtime logs go to `scratch/logs/<campaign>/` and are gitignored.
