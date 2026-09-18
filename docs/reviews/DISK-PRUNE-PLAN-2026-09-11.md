# Disk prune / offload plan — 2026-09-11 (audit only, nothing deleted)

/home: 1.9 TB, 1.6 TB used, 243 GB free at audit time. Q4_K_M download in flight (−119 GB → ~124 GB free).

## Tier 1 — delete now, zero risk (~186 GB)
| Item | Size | Note |
|---|---|---|
| `~/.local/share/Trash` | 90 GB | 4 Fable-Fusion GGUFs already retired from the catalog + old homework zips. Empty the trash. |
| `~/.cache/huggingface/hub/models--google--gemma-4-31b-it` | 59 GB | Safetensors of Gemma 4 31B; we serve the GGUF (which is itself catalog-excluded). Re-downloadable. |
| Docker: `docker system prune -a --volumes` | ~47 GB | 18 GB dead images (firesys/kind/aws-cli/postgres from 4-month-old projects), 24 GB build cache, 5 GB orphan volumes. Re-pulls open-webui + searxng on next start (2 min). |
| `~/.cache/uv` + `~/.cache/pip` + `~/.cache/pypoetry` | 29 GB | Package caches; re-populate on demand. |
| Rest of `~/.cache/huggingface/hub` (SmolLM3, bge-m3, bert-*, PUBMED dataset…) | ~25 GB | Old NLP-course artifacts. Re-downloadable. |
| `~/Downloads/ubuntu-26.04-desktop-amd64.iso` | 6.5 GB | ISO. |

## Tier 2 — delete after a decision (~85 GB)
| Item | Size | Decision needed |
|---|---|---|
| `/var/cache/pacman/pkg` | 35 GB | Install `pacman-contrib`, run `paccache -rk2` (keeps last 2 versions → kernel 7.1.8 rollback stays). ~25–30 GB back. |
| `~/hf_models/*` (Qwen3.5-9B/4B/2B, SocratTeachLLM, glm-4-9b safetensors) | 66 GB | Source checkpoints; the two we use exist as Q8 GGUFs in weights/. Re-downloadable. |
| `weights/SocratTeachLLM-Q8_0.gguf`, `weights/glm-4-9b-chat-Q8_0.gguf` | 19 GB | No serve script references them (unreferenced). |
| Phase D prune (memory, ✋ Max): Heretic v2 Q5 / Qwen3.6-27B-MTP / Qwen3.8-27B-Q6_K | 19+19+22 GB | Catalog-listed; dominated by the current default. Max's call. |

## Tier 3 — OFFLOAD to the external drive (research archive, ~450 GB)
Paper-campaign artifacts. Unique results live in the small files (results-*.txt, logs, adapters); the GGUFs are re-derivable but cost GPU-hours. Move the whole tree, keep the small files.
| Item | Size | Status |
|---|---|---|
| `research/lowrank/experiments/27-bf16-rederivation` | 97 GB | DONE (E27 protocol-v2 rerun); quants re-derivable from BF16 |
| `research/lowrank/experiments/29-srr-split` | 63 GB | DONE (E29 "order doesn't matter"); 55 GB DEFLATED gguf |
| `research/lowrank/experiments/23-moe` | 43 GB | DONE (E23 centerpiece rung) |
| `research/lowrank/experiments/13-rerounder`, `11-alternation`, `10`, `22`, `07`, `04` … | ~75 GB | DONE experiments |
| `research/lowrank/data/gram*` (gram27b, gram27b-v2, gram27b-bf16, gram38-bf16, gram35b) | ~115 GB | Gram matrices — recomputable (hours of GPU each); keep gram38-bf16 if E33/E16 follow-ups continue |
| `research/lowrank/data/*.logits` | ~56 GB | BF16 reference logits — recomputable; `bf16ref38-40.logits` (4.7 GB) is used by the E32 chain → KEEP until campaign done |
| `weights/research-staging/Qwen3.6-27B-uncensored-heretic-v2-…-BF16.gguf` | 51 GB | Heretic BF16 source — only for re-quant research |
| `weights/research-staging/BF16/` (Qwen3.8-27B BF16) | 51 GB | ⚠ USED by E32 fineweb control — KEEP until campaign done |
| `weights/research-staging/*IQ3*/*IQ2*/*Q2_K_XL*` (E32 pairs) | ~48 GB | ⚠ USED by the paused E32 chain — KEEP until campaign done |
| `research/lowrank/experiments/32-…`, `34-…`, `e33`, `e16-08b` | ~20 GB | ACTIVE — keep |

## Tier 4 — not mine to judge
| Item | Size |
|---|---|
| `~/Documents/scu/CSEN-346`, `csen-342` | 190 GB (coursework) |
| `~/Documents/projects` | 26 GB |

## Sequence
1. Tier 1 today (+186 GB) — no drive needed.
2. Tier 2 pacman + hf_models when convenient (+95 GB).
3. Tier 3 offload once the external drive is here; do it AFTER the E32 campaign completes so nothing it reads moves. `rsync -a --remove-source-files` per directory, verify, then delete.
4. Phase D weight prune = Max's call.
