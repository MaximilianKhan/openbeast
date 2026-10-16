# E36-D: small-scale ablations (PLAN.md §0 and §D)

The run scripts for §D (registered 2026-10-15 16:45). Written and checked on
CPU on 2026-10-15. Nothing in this directory has touched the GPU yet. Every
build recipe below was reproduced on CPU against the E22/E24/E26/E27
artifacts before it went into a script (see Validation).

## Scripts

| file | what it does | PLAN §D item |
|---|---|---|
| `d_08b.sh` | 0.8B lane: 3 references, 8 Gram and imatrix captures, every build, scoring on wiki/fw/code, the D0 gate, paired readouts | D0, T1.6, T1.15, T1.1, T1.13 |
| `d_t19_27b.sh` | 27B: quantizes IQ2_XS and IQ1_S, extracts the same adapter on 3 bases, scores on wiki and fw, writes the recovery fractions | T1.9 |
| `e26_equalize_alpha.py` | copy of `26-lloyd-gauge/e26_equalize.py` that adds `--alpha`, `--gram-dir` and an optional imatrix. The original is untouched | T1.13 |
| `d_stats.py` | `gate` (D0 rule), `spread` (T1.15 SD and range), `recovery` (T1.9 fraction with paired bootstrap CI). Uses E24's parser unchanged | D0, T1.15, T1.9 |
| `gram_ok.py` | checks a Gram directory before anything reads it: every file present with the right size, token count, finite and non-negative diagonal | all captures |
| `calib/` | calibration texts, their rebuild scripts, `SHA256SUMS` | §D calibration |
| `validate/` | CPU validation helpers and the stand-in lane log | — |

Both scripts work like `bc_queue.sh`. GPU steps run in the foreground. All
CPU builds run in one background lane with 12 threads (`OMP`/`OPENBLAS`/`MKL`
set to 12, llama-quantize nthreads 12, `CUDA_VISIBLE_DEVICES=""`). The GPU
scorer waits for each artifact in turn. Every step can be resumed:
- builds write to `*.part` and are renamed when they succeed;
- captures are checked with `gram_ok.py`;
- `score` skips any log that already has a KLD summary;
- RR code caches and e27 factor caches carry a fingerprint and are wiped if
  their inputs change. This closes the e13/e27 cache hazard the E27 README
  records.

Run both scripts under `scripts/gpu-lease.sh`.

### `d_08b.sh` (0.8B), in order
1. Checks the calibration texts against `calib/SHA256SUMS`.
2. **GPU:** builds the references `data/e36-bf16ref08b-{wiki,fw,code}-40.logits`
   (BF16, 40×512, `-ngl 99`). Then captures `data/e36-gram08b-wiki` with
   `build-research/bin/llama-imatrix`, `LLAMA_IMATRIX_GRAM_DIR`,
   `--chunks 40 -c 512 -ngl 99`, the E22 budget, with wiki.train as the text.
3. Starts the CPU lane, which builds the D0 artifacts first:
   - Q2_K, IQ3_XXS, Q3_K_M, Q4_K_M and IQ2_M, each one quantize step from
     `Qwen3.5-0.8B-BF16.gguf` with the wiki imatrix and pin `blk\.24\.=q5_k`;
   - RR-wiki;
   - the fc adapter;
   - alternation ×1: W′ = W − C0 (`e11_alternate.py`), quantize W′ the
     same way, then extract C1;
   - after D0: the T1.6 control and the T1.13 files.
4. **GPU:** captures the other 7 calibration sets (code, fw, mix2, m111,
   thirdA/B/C) while the CPU lane works. It writes `.captures-done`, and
   the lane then builds the T1.15 and T1.1 artifacts.
5. **GPU:** scores D0 on all three corpora, then applies the **D0 gate**:
   `d_stats.py gate wiki Q2K-rr-wiki Q2_K -2`, written to
   `logs-d/gate.txt`. If the gate fails, the script exits 2 and scores no
   ablation.
6. **GPU:** scores T1.6, T1.13, T1.15 and T1.1, all on wiki, fw and code.
7. **CPU:** writes `paired-d.txt` (e36_stats.py for every registered pair,
   plus the T1.15 spread for each corpus).

Outputs:
- artifacts in `d08b/art/`;
- Grams in `data/e36-gram08b-*`;
- logs in `logs-d/` (named `<corpus>-<label>.log`);
- rows in `results-d.txt`;
- paired readouts in `paired-d.txt`.

Labels:

| item | labels |
|---|---|
| D0 | `Q2_K`, `IQ3_XXS`, `Q3_K_M`, `Q4_K_M`, `IQ2_M`, `Q2K-rr-wiki`, `Q2K+fc-wiki`, `Q2Kalt1+fc-wiki` (the crown) |
| T1.6 | `CONTROL-A` or `CONTROL-B` |
| T1.13 | `Q2_K-noimx`, `Q2K-eq-a1.00-noimx`, `Q2K-eq-a{0.25,0.50,0.75,1.00}` |
| T1.15 | `Q2K-rr-third{A,B,C}` |
| T1.1 | `Q2K-rr-{code,fw,mix2,m111}`, `Q2K+fc-{code,fw,mix2,m111}` |

The wiki arm of T1.1 is the same pair as D0's `Q2K-rr-wiki` and `Q2K+fc-wiki`
(same base, same Grams, same recipe), so it is not built twice.

Design choices the plan leaves open, made explicit here:
- **T1.1 base.** One shared bare Q2_K (D0's, wiki imatrix) is the base for
  every arm. Only the method's calibration changes: the Grams, and for fc
  also the arm's own imatrix, which only decides which tensors are
  included. This makes "paired against bare Q2_K" a single pair. §B does
  the same at 27B.
- **T1.13 corpora.** The registered readout is wiki. The script also scores
  fw and code, at about 1 GPU-minute each.
- **T1.13 bytes.** The transformed imatrices live in
  `data/e36-gram08b-a{025,050,075,100}`. These are 16-character names, the
  same length as `e36-gram08b-wiki`, so each equalized Q2_K has exactly the
  bare Q2_K's bytes. This is E26's parity trick, and the CPU lane checks it.

### `d_t19_27b.sh` (27B)

Before it starts, the script requires:
- the E36-A references `data/e36-bf16ref27b-{wiki,fw}-100.logits`, with
  "Final estimate" in their logs;
- the Grams `data/gram27b-e36-wiki`: `gram_ok.py` passes at count
  48×512 = 24576, and `logs-b/capture-gram27b-e36-wiki.log` contains
  "Final estimate".

If the Grams are not ready and no 27B `llama-imatrix` is running, the script
exits instead of holding the lease. Run it after `bc_queue.sh`'s captures.

CPU lane:
- **Quantize** `h27bf16-IQ2_XS.gguf` and `h27bf16-IQ1_S.gguf` into `$E36`,
  with the E27 imatrix `data/gram27b-bf16/diag.imatrix.gguf` and pin
  `blk\.64\.=q5_k`.
- **Extract** with `e27_extract.py` on each of Q2_K (E27's file), IQ2_XS
  and IQ1_S, writing `adapter-t19-fc-r128q8-<base>.gguf`. Settings:
  `--rank 128 --rank-map ffn_gate=0,ffn_up=0,ffn_down=0 --q8-factors
  --gram-dir data/gram27b-e36-wiki`, with that capture's own imatrix. Each
  base gets its own fingerprinted cache, and exit code 3 means "resume".

GPU:
- Scores base and base+fc on wiki and fw. Logs go to `logs-a/` and rows to
  `results-d27.txt` (kept separate so 0.8B and 27B rows with the same tag
  never mix).
- The bare Q2_K row is E36-A's `Q2_K` log (same file, reference and build).
  It is reused, never scored twice.

The final file is `recovery-t19.txt`: for each base and corpus, (KLD_base −
KLD_fc)/KLD_base with a 95% paired bootstrap CI (10,000 resamples, seed
20261009), plus the e36_stats paired rows.

## T1.6: the registered control rule cannot reach the crown's bytes at 0.8B

**Crown bytes** = Q2_K + r128 Q8_0 adapter = 436,408,832 + 86,921,376 =
**523,330,208 B**. The parts:
- **Q2_K.** Measured on the current build (b10865) with the gram08b
  imatrix. The header prediction for the `e36-gram08b-wiki` imatrix path
  gives the same padded size; `validate/header_bytes.py` derives it.
- **Adapter.** The size depends only on the layout: 150 tensors (the E22
  target set), Q8_0, r128. It is identical across E22, E24 and E34 and in
  the validation run.

**At 0.8B, Q3_K_M already holds four listed kinds at q4_k or above:**
attn_v, attn_output, attn_qkv (two layers at q5_k) and ffn_down (layer 0
at q5_k). The registered list therefore only touches attn_k, attn_q,
attn_gate, ssm_out, ffn_gate and ffn_up, which are all Q3_K. Promoting all
of them as whole kinds uses up the list:

| recipe | bytes | vs crown | status |
|---|---|---|---|
| **A** = registered list, used up (`CTRL_A` in d_08b.sh) | **517,487,616** | **−1.117%** | follows the rule, but misses the 0.5% band by 0.62 points |
| **B** = A + a second pass at q5_k in the same kind order: attn_k, attn_v, attn_output as whole kinds, then attn_qkv on 4 of its 16 q4_k layers, evenly spaced (blk 2, 9, 16, 22) | **522,992,640** | **−0.0645%** | inside the band, but **not the registered rule** |

Both were built on CPU, and the sizes are exact. Recipe B's second step is
an extension I wrote; the rule does not give it. Bytes for the arithmetic:
- per block: Q3_K 110 B, Q4_K 144 B, Q5_K 176 B;
- step sizes: attn_k and attn_v 65,536 B per tensor; attn_output 262,144;
  attn_qkv 786,432;
- B is A + 393,216 + 393,216 + 1,572,864 + 4 × 786,432.

**`d_08b.sh` builds no control until someone picks a recipe:**
`T16_RECIPE=A` or `T16_RECIPE=B`. If neither is set, T1.6 is logged as
BLOCKED and everything else runs. Adding the control later is resume-safe,
and it is still one build and one score.

**"No imatrix gives the same bytes".** This holds for tensor data, not for
the file. Without `--imatrix`, tensor types and sizes are identical
(checked per tensor on Q2_K, Q3_K_M and recipe A). The file is 352 B
smaller because four `quantize.imatrix.*` KV entries are missing from the
header. So the control's bytes are counted the way the base's are: with
the same imatrix in both.

## Calibration texts (`calib/`)

Paths in `SHA256SUMS` are relative to `research/lowrank`. Rebuild with:
- `python3 -I calib/fetch_fineweb_calib.py calib/fineweb-edu-calib.txt calib/fineweb-edu-calib.manifest.tsv experiments/32-t117-gsq-head-to-head/fineweb-edu-control.txt`
  (network);
- `python3 calib/build_calib.py --check-tokens`, which refuses if a rebuilt
  file differs from `SHA256SUMS`.

| arm | file | bytes | sha256 | tokens |
|---|---|---|---|---|
| wiki | `data/wikitext-2-raw/wiki.train.raw` | 10,940,747 | 6707892f… | 2,505,800 |
| code | `experiments/17-task-conditioned/code-train.txt` | 3,000,000 | 3540bfae… | 933,270 |
| fw | `calib/fineweb-edu-calib.txt` | 3,020,894 | ec946576… | 678,611 |
| mix2 (2:1, E22) | `data/mixed-calib2.txt` | 3,000,000 | 28f156fe… (matches HELDOUT-METHOD) | 860,321 |
| m111 (1:1:1) | `calib/mix111.txt` | 3,000,000 | 29eb09a7… | 847,478 |
| T1.15 A | `calib/wiki-train-third-a.txt` = wiki.train bytes [0, 3647858) | 3,647,858 | 9bb59bbf… | 832,781 |
| T1.15 B | `calib/wiki-train-third-b.txt` = [3647858, 7294712) | 3,646,854 | 478b5f0b… | 845,565 |
| T1.15 C | `calib/wiki-train-third-c.txt` = [7294712, 10940747) | 3,646,035 | ac57980f… | 827,454 |

Every file holds ≥ 20,481 tokens (40×512+1), counted with llama-tokenize on
CPU. The full hashes are in `SHA256SUMS`.

- **FineWeb-Edu calibration.** sample-10BT rows **2000..2647** (648 whole
  documents, joined by `\n\n`), taken from the datasets-server, the same
  pull path as E32. The script re-pulled rows 0..399 and confirmed they
  rebuild E32's `fineweb-edu-control.txt` exactly. Disjointness from that
  eval text, checked three ways:
  - 0 shared row ids;
  - 0 shared document texts (sha256);
  - 15 matching 64-byte shingles in 13 documents (at most 3 of 27 in any
    document). All 15 are site boilerplate ("Note: Materials may be edited
    for content…", "Discover the cosmos!…", GNU FDL footers), not shared
    documents.

  `calib/fineweb-edu-calib.manifest.tsv` lists every row index, id and
  sha256.
- **m111** follows `build_mixed_calib.sh`'s slicing style: the first 1 MB
  of wiki.train, code-train and fw-calib, in 2000-byte raw slices taken in
  turn.
- **Thirds.** Cut at n/3 and 2n/3, each cut **moved forward to the next
  newline** so no slice starts mid-line or mid-UTF-8 character. Each
  boundary moves by less than one line. Slice A starts at byte 0, so its
  first 40 chunks are the same text D0's wiki capture reads. RR-thirdA
  should therefore come out byte-identical to RR-wiki, unless GPU capture
  is nondeterministic. This is a free determinism check.
- **Disjointness.** Every calibration file was also probed for 200-byte
  windows of wiki.test, code-test and the FineWeb eval text. There were 0
  hits.

## Validation (CPU only, 2026-10-15; nothing kept from stand-ins)

GPU use: none. `CUDA_VISIBLE_DEVICES=""` was set on every llama.cpp binary,
with at most 12 threads.
- **Stand-in lane.** The whole CPU lane of `d_08b.sh` (all 27 artifacts,
  `T16_RECIPE=B`) ran in `D08B_CPU_ONLY` mode, with the pre-fix `gram08b`
  and `gram08b-mixed` Grams symlinked in as stand-ins. It ran in 25 min
  (log: `validate/cpu-lane-validation.log`); every artifact was then
  deleted. Because the stand-in Grams are the ones E22, E24 and E26 used,
  the lane must reproduce those papers' files, and it did:
  - Q2_K and alt1: 335/335 tensors byte-identical to E22's
    `q35-08b-Q2_K.gguf` and `q35-08b-Q2K-alt1.gguf`, so the current build
    quantizes the same as b10254;
  - W′ byte-identical to E22's `q35-Wprime.gguf` (checked separately);
  - RR-wiki 335/335 byte-identical to E24's `q35-08b-Q2K-rr-input.gguf`;
    RR on `gram08b-mixed` 335/335 identical to E22's
    `q35-08b-Q2K-rr-mixed2.gguf`;
  - fc-wiki and fc-alt1 match E22's `fc-r128q8.gguf` and
    `fc-alt1-r128q8.gguf`: 149/150 tensors byte-identical; the 150th is a
    sign flip of the singular vector, with B·A identical (relative
    difference 0);
  - eq α=1 335/335 byte-identical to E26's `q35-08b-Q2K-eq.gguf`.

  The byte-parity and crown-bytes checks fired as designed. The validation
  paths are longer than the real ones, which adds 32 B of header padding;
  with the production paths the prediction is exact.
- **The E22 extraction recipe is the exact float64 SVD**
  (`extract_adapter.py` without `--rsvd/--compute32`). The rsvd path that
  E34 used at 0.8B differs from E22's adapter by 16–27% in B·A on the
  tensors checked, so `d_08b.sh` uses the exact path.
- **Equalize copy.**
  - α=1: model and imatrix byte-identical to the original
    `e26_equalize.py` output, and to E26's stored `q35-08b-BF16-eq.gguf`
    and `imx-eq.gguf`.
  - No-imatrix mode gives the identical model.
  - α=0.25 and 0.5 run, with s ranges [1,2] and [1,4] vs [0.5,16] at
    α=1. α=0.75 ran in the lane. All of them quantize to exactly Q2_K's
    bytes.
- **Quantize recipes.**
  - 0.8B Q2_K, IQ3_XXS, Q3_K_M, Q4_K_M, IQ2_M, Q2_K-noimx, controls A and
    B, IQ2_XS and IQ1_S all build.
  - 27B IQ2_XS and IQ1_S: `--dry-run` with the E27 imatrix and pin
    succeeds. `validate/check_dry_imatrix.py` confirms every tensor that
    needs an imatrix has an entry (0 missing). Expected size: IQ2_XS
    8,937 MiB, IQ1_S 7,087 MiB of tensors.
- **RR.** 3 tensors (with `--max-tensors 3`) matched E26's fresh codes
  byte for byte. A full run took 79 s.
- **27B extraction.** `e27_extract.py` ran on E27's Q2_K with
  `gram27b-bf16` (stand-in) and a 30 s budget: 17 tensors, then exit 3
  (resume). 16 of 17 factor pairs are bit-identical to E27's `fc-cache`;
  1 differs at 9e-9, which is float32 BLAS noise. On 0.8B IQ2_XS and IQ1_S
  bases it runs and resumes, so gguf-py dequantizes both IQ types.
- **Statistics helpers.** `d_stats.py` gate and recovery on E34's logs give
  t = −31.98 and recovery 0.4993 [0.4833, 0.5159]. The JOURNAL says t =
  −32.0 and 50%.
- `bash -n` and `shellcheck -S warning` are clean on both scripts and on
  `bc_queue.sh`.

## Time estimates

These are measured where possible, at 12 CPU threads.

| step | estimate | basis |
|---|---|---|
| 0.8B refs ×3 | GPU ~3 min | 40 chunks of BF16 plus writing 5.1 GB of logits each |
| 0.8B captures ×8 | GPU ~10–15 min | 40 chunks each, plus 1.8 GB of Grams each |
| 0.8B CPU lane | **25 min** total, measured; D0 part 7.5 min | quantize 5–20 s each; RR 80–90 s each (×8); fc extract 100–115 s each (×6); equalize 9 s each |
| 0.8B scoring | GPU ~40 min | 78 scores at ~30 s (0.45 s per chunk at 0.8B plus loading 5 GB of reference) |
| **d_08b.sh wall** | **~1–1.25 h** | the CPU lane overlaps the captures and scoring |
| 27B quantize IQ2_XS / IQ1_S | CPU ~14 / ~6 min | 0.8B timings (23.7 s / 9.9 s) × 36 |
| 27B extraction ×3 | CPU ~7–8 min each, ~25 min total | 17 tensors in 33 s; 208 tensors per adapter |
| 27B scoring | GPU ~10 min | 10 scores (Q2_K reused from A) at ~25–45 s each |
| **d_t19_27b.sh wall** | **~50 min** | set by the CPU lane; the GPU sits idle for most of it under the lease |

**Disk:**
- 0.8B: about 15 GB of references, 14 GB of Grams and 12 GB of artifacts.
- 27B: IQ2_XS about 9.4 GB, IQ1_S about 7.4 GB, plus 3 × 0.34 GB of
  adapters and factor caches of a similar size.

**Option, not wired in.** The 27B quantize step does not need the GPU. Run
it before taking the lease to cut about 20 min of idle lease time.
