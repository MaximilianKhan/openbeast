# Supplementary material: manifest

Each top-level item below is one entry of the paper's Appendix B ("Supplementary material index"); a bracketed tag such as [Supp. E24] in the paper names the item. Paths are relative to the root of this archive. Paths in the files are written `<ROOT>/...` for the root of this archive (the directory the experiments ran in), `<REPO>/...` for the repository that held it (the llama.cpp build lives there) and `<HOME>/...` for the user's home directory; in shell scripts the same three are `${SUPP_ROOT}`, `${SUPP_REPO}` and `${HOME}`, so the scripts still parse. Names of the authors, their accounts, machines and own repositories, and commit hashes of their own repositories (`<commit>`), have been replaced mechanically. Upstream llama.cpp build numbers (for example b10865) and third-party commit hashes are unchanged.

Only text is included: scripts, logs, results tables and notes. Model weights (GGUF), cached logits, Gram / covariance matrices (`.npy`, `.npz`), dumped quantization codes and copies of public corpora are not, either because of their size (tens of GB per experiment) or because they are third-party data that the included scripts regenerate; the counts are listed per item. The text corpora used for calibration and evaluation are public (wikitext-2, FineWeb-Edu, HellaSwag) except the code corpus of E17, which was cut from the authors' own source tree and is withheld; the excluded copies are listed with their sha256 and the included script that rebuilds and checks each one. The shared data directory the scripts read from (`<ROOT>/data/`: reference logits, Gram captures, imatrix files, corpora) is not cited as an item and is not included.

## Items

| item | location | cited in | what it is | files shipped |
|---|---|---|---|---|
| E1 | `experiments/01-svd-spectrum/` | §1.2, §4.1 | E01 — SVD spectrum census of Qwen3-0.6B | 3 (24.0 KB) |
| E3 | `experiments/03-residual-rank/` | §1.2, §4.1 | E03 — Is quantization error low-rank? | 3 (23.2 KB) |
| E10 | `experiments/10-full-covariance/` | §2.1, §4.7 | E10 — full-covariance whitening: the paradigm's second act | 2 (10.5 KB) |
| E13 | `experiments/13-rerounder/` | §2.3, §3.1, §3.3, §4.9 | E13 — frozen-grid error-feedback re-rounding ("GPTQ for K-quants") | 3 (16.5 KB) |
| E14 | `experiments/14-fused-kernel/` | §1.3, §6.2 | Experiment 14 — Phase 1 fused adapter epilogue (B·t + scale + add into mmvq) | 4 (38.5 KB) |
| E15 | `experiments/15-iq1-carrier/` | §A.2 | E15 — the IQ1_S carrier: the original pure-factorization goal in servable form | 2 (3.7 KB) |
| E17 | `experiments/17-task-conditioned/` | §4.8 | E14-cond — task-conditioned correction (dir: 17-task-conditioned) | 2 (2.3 KB) |
| E20 | `experiments/20-gradgram/` | §2.1, §4.7 | E20 - output-gradient Gram capture (grad Grams for M2/M3) | 2 (13.0 KB) |
| E23 | `experiments/23-moe/` | §2.1, §4.6 | E23 — MoE expert-stack correction on Qwen3.6-35B-A3B (the centerpiece rung) | 15 (439.3 KB) |
| E24 | `experiments/24-yaqa-lite/` | §2.3, §3.2, §3.3 | E24 — YAQA-lite: Kronecker-global-metric re-rounding (M1) on Qwen3.5-0.8B | 18 (147.7 KB) |
| E25 | `experiments/25-alloc-concurrency/` | §1.3, §6.3 | Experiment 25 - allocator-level concurrency fix for GGML_CUDA_GRAPH_OPT | 1 (10.5 KB) |
| E26 | `experiments/26-lloyd-gauge/` | §7.3, §A.3, §A.4 | E26 — Fisher-Lloyd fixed point (M4) + principled gauge-fixing (M5) | 28 (278.9 KB) |
| E27 | `experiments/27-bf16-rederivation/` | §2, §4.3, §6.2 | E27 — the 27B story, re-derived BF16-pure (PROTOCOL v2 rerun) | 64 (2.6 MB) |
| E28 | `experiments/28-glowq-shared-a/` | §4.4 | E28 — GlowQ shared-A: input-sharing groups share whitened residual subspaces — GO | 9 (34.0 KB) |
| E29 | `experiments/29-srr-split/` | §4.4 | E29 — SRR preserve-then-quantize split at 27B: order doesn't matter | 9 (183.0 KB) |
| E31 | `experiments/31-seeded-basis-duel/` | §4.4 | E31 — AWSRC seeded-basis vs learned whitened factors (capture-per-byte duel) | 3 (11.7 KB) |
| E32 | `experiments/32-t117-gsq-head-to-head/` | §5.1, §5.2, §5.3, §5.4, §5.5 | E32 / T1.17 — GSQ-RCO head-to-head on Qwen3.8-27B (staged 2026-09-09) | 44 (284.0 KB) |
| E33 | `experiments/33-t110-capture-width/` | §4.2 | E33 / T1.10 — capture-vs-width regression (CPU only, 2026-09-11) | 22 (672.1 KB) |
| E34 | `experiments/34-e16-nvfp4-08b/` | §A.1 | E34 / E16 rung 1 — whitened low-rank correction on a self-quantized NVFP4 base, 0.8B (2026-09-11) | 34 (847.7 KB) |
| E35 | `experiments/35-final-reanalysis/` | §2.5, §3.1, §4.3, §4.4, §4.6, §5.2, §5.3, §5.4, §6.1, §7.3, §A.2 | E35 — final reanalysis for the paper revision (2026-10-09) | 3 (60.0 KB) |
| E36 | `experiments/36-submission/` | Abstract, §1.2, §1.3, §3.1, §3.4, §4.3, §4.5, §5.4, §7.2, §A.3 | E36 — the submission campaign (pre-registered 2026-10-15) | 266 (3.6 MB) |
| ablation | `paper/ABLATION-PLAN.md` | §7.2 | the missing-runs matrix the final experiments were planned from | 1 (33.5 KB) |
| journal | `JOURNAL.md` | §2.2, §3.2, §4.3, §4.7, §5.3, §7.6 | the timestamped experiment log (pre-registrations, decisions, incidents), in order of writing | 1 (92.1 KB) |
| kernels | `kernels/` | §2, §3, §6 (the patched llama-imatrix and kernels; not tagged) | the research-side llama.cpp changes as `git format-patch` files against upstream llama.cpp (correction kernels, re-rounding support, Gram/gradient capture), with their README | 3 (101.8 KB) |
| manifolds | `prior-art/MANIFOLD-CANDIDATES.md` | §7.4 | literature note: manifold / information-geometry candidates (authored notes on cited work) | 1 (7.2 KB) |
| protocol | `PROTOCOL.md` | §2.5 | the measurement protocol (methodology v2) the experiments follow | 1 (4.0 KB) |
| review | `review/` | §2.2, §4, §4.2, §4.3, §4.7, §4.9, §6.1, §7.1, §7.6, §A.2 | the adversarial reviews of claims, code, statistics and the paper, and the coherence audit | 7 (134.8 KB) |
| rollup | `RESULTS_ROLLUP.md` | §1.1, §4.2, §4.3, §6.1 | consolidated results across experiments, with the corrections log | 1 (20.3 KB) |
| theory-L6 | `paper/theory-L6-family-subsumption.md` | §2.2, §2.3, §8 | theory note: family subsumption (L6) | 1 (20.5 KB) |
| theory-alt | `paper/theory-alternation-convergence.md` | §2.4, §A.5 | theory note: convergence of the alternation | 1 (4.2 KB) |
| wins | `paper/DEPLOYABLE-WINS.md` | §7.4 | the deployable-results summary, claims calibrated to the reviews | 1 (8.4 KB) |

## Excluded, by item and reason

| item | reason | files | size |
|---|---|---|---|
| E1 | binary or non-text type `.npz` | 1 | 767.6 KB |
| E3 | binary or non-text type `.gguf` | 1 | 296.2 MB |
| E3 | binary or non-text type `.npz` | 1 | 765.1 KB |
| E10 | binary or non-text type `.gguf` | 7 | 5.1 GB |
| E13 | binary or non-text type `.gguf` | 9 | 12.6 GB |
| E13 | cache / code-dump directory `__pycache__/` | 1 | 18.3 KB |
| E13 | cache / code-dump directory `codes27b/` | 256 | 15.5 GB |
| E15 | binary or non-text type `.gguf` | 2 | 3.3 GB |
| E17 | binary or non-text type `.gguf` | 1 | 80.8 MB |
| E17 | corpus built from the authors' own source code | 3 | 7.0 MB |
| E20 | binary or non-text type `.gguf` | 2 | 161.5 MB |
| E23 | binary or non-text type `.gguf` | 5 | 45.7 GB |
| E24 | binary or non-text type `.gguf` | 3 | 959.7 MB |
| E24 | cache / code-dump directory `__pycache__/` | 2 | 32.3 KB |
| E24 | cache / code-dump directory `codes-input/` | 97 | 280.0 MB |
| E24 | cache / code-dump directory `codes-kron/` | 97 | 280.0 MB |
| E26 | binary or non-text type `.gguf` | 5 | 2.9 GB |
| E26 | binary or non-text type `.npz` | 96 | 288.8 MB |
| E26 | cache / code-dump directory `__pycache__/` | 2 | 33.6 KB |
| E26 | cache / code-dump directory `codes-repro/` | 97 | 280.0 MB |
| E27 | binary or non-text type `.gguf` | 11 | 103.3 GB |
| E27 | cache / code-dump directory `fc-cache/` | 208 | 1.3 GB |
| E29 | binary or non-text type `.gguf` | 3 | 67.2 GB |
| E32 | binary or non-text type `.gguf` | 3 | 2.7 GB |
| E32 | copy of a public text corpus | 1 | 1.7 MB |
| E33 | binary or non-text type `.png` | 3 | 490.8 KB |
| E34 | binary or non-text type `.gguf` | 2 | 173.8 MB |
| E35 | cache / code-dump directory `__pycache__/` | 1 | 48.5 KB |
| E36 | binary or non-text type `.captures-done` | 1 | - |
| E36 | binary or non-text type `.gguf` | 37 | 60.9 GB |
| E36 | cache / code-dump directory `codes-08b-wiki/` | 96 | 91.9 MB |
| E36 | cache / code-dump directory `codes-27b-mixed/` | 256 | 5.1 GB |
| E36 | cache / code-dump directory `codes-rr-code/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-fw/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-m111/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-mix2/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-thirdA/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-thirdB/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-thirdC/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes-rr-wiki/` | 97 | 280.0 MB |
| E36 | cache / code-dump directory `codes27b-mixed/` | 95 | 5.7 GB |
| E36 | cache / code-dump directory `codes27b-wiki/` | 256 | 15.5 GB |
| E36 | cache / code-dump directory `fc-cache-t19-IQ1_S/` | 209 | 1.3 GB |
| E36 | cache / code-dump directory `fc-cache-t19-IQ2_XS/` | 209 | 1.3 GB |
| E36 | cache / code-dump directory `fc-cache-t19-Q2_K/` | 209 | 1.3 GB |
| E36 | copy of a public text corpus | 5 | 17.0 MB |

## Excluded text files

Each is listed with its sha256 and, for corpora, where it comes from and which included script rebuilds and checks it.

| file | size | sha256 | reason | source / rebuild |
|---|---|---|---|---|
| `experiments/17-task-conditioned/code-calib.txt` | 3.6 MB | `c3e3072aeccddae864a285dc205124a1c5721a105abc171b0edf8dad3810e33a` | corpus built from the authors' own source code | source code of the authors' own project and of llama.cpp (identifying, so not shipped); no build script in the archive. Construction: `experiments/17-task-conditioned/README.md`; its uses and hashes: `experiments/36-submission/d08b/README.md`, `experiments/36-submission/d08b/calib/SHA256SUMS` |
| `experiments/17-task-conditioned/code-test.txt` | 400.0 KB | `19efa4ca24eaec8e902bfb301dd3ec206214f9b8a85af859d1cbfe04c5ab6231` | corpus built from the authors' own source code | source code of the authors' own project and of llama.cpp (identifying, so not shipped); no build script in the archive. Construction: `experiments/17-task-conditioned/README.md`; its uses and hashes: `experiments/36-submission/d08b/README.md`, `experiments/36-submission/d08b/calib/SHA256SUMS` |
| `experiments/17-task-conditioned/code-train.txt` | 3.0 MB | `3540bfaed4f8093fcde126ab92824186cd08b54f7ab87ab718c5ee1b3b4bb53a` | corpus built from the authors' own source code | source code of the authors' own project and of llama.cpp (identifying, so not shipped); no build script in the archive. Construction: `experiments/17-task-conditioned/README.md`; its uses and hashes: `experiments/36-submission/d08b/README.md`, `experiments/36-submission/d08b/calib/SHA256SUMS` |
| `experiments/32-t117-gsq-head-to-head/fineweb-edu-control.txt` | 1.7 MB | `60bdd64474d9dbc38807230f7de53a0d5618e69cf4c0cd60575b358dbcd4d18a` | copy of a public text corpus | FineWeb-Edu sample-10BT rows 0..399 joined by blank lines (HF datasets-server); `experiments/36-submission/d08b/calib/fetch_fineweb_calib.py` re-pulls these rows and refuses unless they equal this file; pull described in `experiments/32-t117-gsq-head-to-head/fineweb_control.sh` |
| `experiments/36-submission/d08b/calib/fineweb-edu-calib.txt` | 3.0 MB | `ec94657650ffb9b2601f0fe2055d1237ae0a5d81b332929cfacbd9605ef9581a` | copy of a public text corpus | FineWeb-Edu sample-10BT rows from 2000 up; rebuilt by `experiments/36-submission/d08b/calib/fetch_fineweb_calib.py` (row list: `fineweb-edu-calib.manifest.tsv`), checked by `build_calib.py` against `SHA256SUMS` |
| `experiments/36-submission/d08b/calib/mix111.txt` | 3.0 MB | `29eb09a7dbea6c1d4605e4570c73e50e9b99bf3255d9f13edeae958465916d81` | copy of a public text corpus | derived from wikitext-2 train (+ code-train, FineWeb-Edu calib for mix111); rebuilt and sha256-checked by `experiments/36-submission/d08b/calib/build_calib.py` against `SHA256SUMS` |
| `experiments/36-submission/d08b/calib/wiki-train-third-a.txt` | 3.6 MB | `9bb59bbf3f4731285932332b00b1a59b1446c6ae16c3822bc0c49113585d48a8` | copy of a public text corpus | derived from wikitext-2 train (+ code-train, FineWeb-Edu calib for mix111); rebuilt and sha256-checked by `experiments/36-submission/d08b/calib/build_calib.py` against `SHA256SUMS` |
| `experiments/36-submission/d08b/calib/wiki-train-third-b.txt` | 3.6 MB | `478b5f0b0eefe5bd451a1e452e8a61868e4717cf6a690481dd6a4c9be644584f` | copy of a public text corpus | derived from wikitext-2 train (+ code-train, FineWeb-Edu calib for mix111); rebuilt and sha256-checked by `experiments/36-submission/d08b/calib/build_calib.py` against `SHA256SUMS` |
| `experiments/36-submission/d08b/calib/wiki-train-third-c.txt` | 3.6 MB | `ac57980fb1e6eaea374e2c3f6bd6e36498ed52b6d359b750aaafbb369ff5355f` | copy of a public text corpus | derived from wikitext-2 train (+ code-train, FineWeb-Edu calib for mix111); rebuilt and sha256-checked by `experiments/36-submission/d08b/calib/build_calib.py` against `SHA256SUMS` |

Not included: the paper's own sources and build scripts (the PDF is the submission), and notes the paper does not cite.
