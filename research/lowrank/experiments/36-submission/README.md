# E36 — the submission campaign

Status as of 2026-10-22. Pre-registration: [`PLAN.md`](PLAN.md) (registered
2026-10-15, with dated amendments; never edited after a run). Every part
except F is done and reported in the paper (`../../paper/final/sections/`).
Statistics for every paired table come from `e36_stats.py`; shared shell
helpers are in `lib.sh`.

| part | what | status | results |
|---|---|---|---|
| A | 27B ladder on b10865, three corpora, 100 chunks (`a_ladder27.sh`) | done | [`RESULTS-A.md`](RESULTS-A.md), `results-a.txt`, `logs-a/` |
| A-full | amendment: all 580 wikitext-2 test chunks at 27B (`a_fullwiki.sh`, run via `handoff_afull.sh` at an F row boundary) | done 2026-10-16 | `RESULTS-A.md` "Whole test set", `results-afull.txt`, `logs-afull/` |
| — | chunk splits and cross-build correlations of A (CPU, from existing logs) | done 2026-10-22 | `RESULTS-A.md` "Chunk splits and cross-build agreement" |
| B | 27B re-rounder, single-step provenance, IQ2_M / IQ3_XXS brackets (`bc_queue.sh`) | done | [`RESULTS-B.md`](RESULTS-B.md), `results-b.txt` |
| B5 | diagnostic: re-rounding headroom (swept tensors at Q8_0) (`b5_oracle.sh`) | done | `RESULTS-B.md` "Diagnostics B5 and B6" |
| B6 | diagnostic: the same sweep with the pre-fix E27 Grams (`b6_oldgram.sh`) | done | `RESULTS-B.md` "Diagnostics B5 and B6" |
| — | independent dequantization check of three re-rounded 27B tensors (CPU) | done | `checks/rr27b-independent-dequant.txt`, `checks/rr27b_check.py` |
| C | KLD at a 2,048-token context for the E32 files (`bc_queue.sh`) | done | [`RESULTS-C.md`](RESULTS-C.md), `results-c.txt` |
| D | Qwen3.5-0.8B on b10865: T1.1, T1.6 (0.8B), T1.13, T1.15, brackets (`d08b/`) | done | [`RESULTS-D.md`](RESULTS-D.md), `results-d.txt`, `paired-d.txt`, `logs-d/` |
| T1.9 | recovery curve at 27B, recipe held fixed (`d08b/d_t19_27b.sh`) | done | `RESULTS-D.md` §T1.9, `recovery-t19.txt`, `results-d27.txt` |
| E | free-grid GPTQ against frozen-grid re-rounding (`freegrid/`, `e_freegrid27_cpu.sh`, `e_score.sh`) | done | [`RESULTS-E.md`](RESULTS-E.md) |
| G | Llama-3.1-8B, the non-Qwen model (`llama8b/`) | done | [`RESULTS-G.md`](RESULTS-G.md), `llama8b/` |
| F | capability replication: GSQ-RCO IQ3_S, UD-IQ3_S, UD-IQ3_S + our correction, three runs each, 112-unit suite (`f_capability.sh`) | **running** | `rows-f.txt`, `logs-f/`; analysis `f_analysis.py`; paper text `F-DRAFT.md` |

Large files deleted to reclaim disk are listed in `deleted-2026-10-16.txt`.
The A-full BF16 reference (73.5 GB) is
`research/lowrank/data/e36-bf16ref27b-wiki-full.logits`, outside git; it is to
be deleted when the PR is opened.

## F — capability replication (in progress)

Rule and analysis are registered in `PLAN.md` §F. `f_capability.sh` runs
rounds of (GSQ, UD, OURS) so that stopping at a round boundary leaves the arms
balanced; each completed row appends one line to `rows-f.txt` (alias, result
JSON, CUDA-error count, repo commit, end time). `lora_probe.sh` (read-only)
records `GET /lora-adapters` for each OURS row in `logs-f/lora-*.json`, as proof
the adapter was live. No F result is stated anywhere until all three rounds
are in.

Resume (stack down, GPU free, from the repo root). The script is resume-safe:
rows already in `rows-f.txt` are skipped. A row cut off mid-run is not in
`rows-f.txt` and reruns from scratch; move its partial `logs-f/cap-*.log`
aside first if it should be kept (as `cap-e36f-GSQ-IQ3S-r2.interrupted-20261017.log`
was).

```sh
nohup ./scripts/gpu-lease.sh run "E36 f-cap" -- \
  bash research/lowrank/experiments/36-submission/f_capability.sh \
  > research/lowrank/experiments/36-submission/run-f-cap-resumeN.log 2>&1 &
nohup bash research/lowrank/experiments/36-submission/lora_probe.sh \
  > research/lowrank/experiments/36-submission/run-lora-probe.log 2>&1 &
```

When all nine rows are in:

1. `python3 f_analysis.py` (refuses unless every arm has three runs;
   `--allow-partial` gives a labelled INTERIM view) and `python3 f_analysis.py --md`
   for the two §5.5 tables.
2. Fill the six `[PENDING:F]` markers by the procedure at the top of
   `F-DRAFT.md`.
3. The remaining steps (audit, `build.py --final`, supplementary, PR) are in
   `../../paper/final/README.md` and `../../TODO.md`.
