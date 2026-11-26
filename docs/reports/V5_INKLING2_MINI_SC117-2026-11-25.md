# v5 report: SC117's abliterated Inkling 2 Mini on Basalt, arm I_T2_sc117 (2026-11-25)

> **Update, the same day: Max made this pack the RTX 5090 default**
> ([DECISIONS-2026-11-25 §7](DECISIONS-2026-11-25.md#7-sc117s-abliterated-inkling-2-mini-is-the-rtx-5090-default): the
> uncensored-preferred policy; profile `inkling2-mini-sc117`, the stock pack
> the rollback). §0's last point and §9's points 1-3 record the
> recommendation before that decision.

> **Final (2026-11-25).** Max approved Path B of the Phase 1 review
> ([SC117_INKLING_STRATA-2026-11-25.md](SC117_INKLING_STRATA-2026-11-25.md),
> PR #328): SC117's abliterated Inkling 2 Mini on Basalt via Basalt's own
> converter, then a v5 arm. We moved SC117's 20 edited attention-output
> matrices from its Strata SQ-20 pack into Thinking Machines' release,
> converted the result with the converter that rebuilds Basalt's stock pack
> byte for byte, and ran arm `I_T2_sc117`: **INK-T2-STOCK exactly, the pack
> the only variable.** It finished **51/71, SCORE 75.3**, 0 units lost, 0
> excluded, no context overflow: board row `INK-T2-SC117`, the SC117 pack's
> row, not the default. Decisions:
> [DECISIONS-2026-11-25.md](DECISIONS-2026-11-25.md) §6. The arm and the pack
> recipe: PR #331 (merge `b054d05a`).

## Contents

0. [Summary](#0-summary)
1. [The question and the design](#1-the-question-and-the-design)
2. [How the pack was made](#2-how-the-pack-was-made)
3. [Serve checks, speed and behaviour (Phase B)](#3-serve-checks-speed-and-behaviour-phase-b)
4. [The row](#4-the-row)
5. [Tables (generated)](#5-tables-generated)
6. [Paired comparisons](#6-paired-comparisons)
7. [Time, tokens, the server log and #588](#7-time-tokens-the-server-log-and-588)
8. [Integrity and the dlsym stop](#8-integrity-and-the-dlsym-stop)
9. [Verdict and recommendations](#9-verdict-and-recommendations)
10. [Caveats](#10-caveats)
11. [Sources and how to regenerate](#11-sources-and-how-to-regenerate)

---

## 0. Summary

- **The converter reproduces the stock pack byte for byte.** Basalt v0.5.7's
  `tools/convert` on `thinkingmachines/Inkling-2-Mini @ b85c3cfa` (the
  revision named inside the stock pack) writes `28ec7072…`, the exact file
  INK-T2-STOCK served. So the comparison below is between two outputs of one
  deterministic tool on inputs that differ in 20 tensors (§2.2).
- **The transplant is exact and confined.** The official SQ-20 stores all 24
  `attn.wo_ud` matrices as the release's own BF16 bytes; SC117's SQ-20
  differs from it only inside layers 4-23's (no other byte). Written into the
  release, exactly 20 of 1,244 checkpoint tensors differ; the converted pack
  `Inkling-2-Mini-SC117-abliterated-NVFP4-MX8.basalt` (sha256 `6eaa50d3…`)
  differs from stock only in `blk.{4..23}.attn_output.weight` (Q8_0) and one
  meta string. Rebuilt offline by
  [`scripts/engines/inkling-sc117-pack.sh`](../../scripts/engines/inkling-sc117-pack.sh),
  pinned in `scripts/weights.registry` (§2).
- **Speed: identical.** Same-day control on the unpatched build, the same
  prompts: decode 418.7 / 344.0 tok/s (code / prose, thinking `none`)
  against stock's 416.1 / 339.1; prefill within 0.2% (§3.1). In the arm,
  457 tok/s over all replies (stock's arm: 444).
- **The edit moves the model more than Blackfrost's did.** KLD against
  UD-Q8_K_XL 0.0354 (stock 0.0128, Derisked 0.0263), top-1 92.18%,
  perplexity +1.6% over the base (§3.2). Touching 20 matrices did not make
  the edit small in effect.
- **Behaviour.** Thinking, `reasoning_content` and tool calls work as on
  stock (12/12 probe requests, conformance PASS at c=1 and c=4). Six benign
  professional prompts: both packs answered all six, no refusals; stock
  hedged two openings, SC117 none. The stock pack declined the 16K needle
  once (a planted passphrase); SC117 returned it (§3.3, §3.4).
- **v5: 51/71 against INK-T2-STOCK's 53/71, the one-variable test.** Paired
  over all 71 units: 1 gained, 3 lost, net −2, exact McNemar p 0.63; the
  minimum detectable effect is 9.5 units at the tool's churn (about 7 at
  Inkling's measured seed churn). **Level within noise; no sign the edit
  helps coding, and no measurable cost** (§6).
- **Against Blackfrost's Derisked (Max's question, "better than
  Derisked?"):** 51 against 49, 3 gained, 1 lost, p 0.63; against Derisked's
  seed repeat (51) 1 and 1. **Level within noise**, with fewer empty capped
  turns (87 against 203) at the same speed. Against Q38U-T2 over the 63
  units both measured cleanly: 2 gained, 5 lost (p 0.45).
- **Clean run.** 0 contaminated (five refused `web_search` calls); no
  overflow; 0 wall hits; 87 empty capped turns (the unpatched build's #588;
  INK-T2-STOCK 105), equal to the server log's own count; `122_gemm_blocked_d`
  stopped at the Linux `dlsym` check again and replays PASS 3/3 (§7, §8).
- **Verdict** (§9, DECISIONS-2026-11-25 §6): the RTX 5090 default stays
  INK-T2-STOCK. SC117's pack is the better of the two abliterated Inkling
  packs we have measured (level on v5, fewer empty turns, the cleaner
  provenance: a pinned 20-tensor edit of a pinned release), so it replaces
  Derisked as the abliterated alternative **if** Max wants one on the 5090;
  that and any default change are his call.

## 1. The question and the design

Max's question (2026-11-25): is SC117's abliterated Inkling 2 Mini better
than the Derisked pack, with our optimizations, and only if it runs with
thinking on. The Phase 1 review found the edit small in scope (20 BF16
matrices) and its provenance clean, Strata about half Basalt's speed on this
pack family, and recommended Path B: the edit on Basalt, measured as an arm
that differs from INK-T2-STOCK only in the weights.

**One variable.** `I_T2_sc117` (campaign `v5-thinking-first`, PR #331) is
`I_T2_stock` with its own model name: the same harness9 tree (`f38a29c7`,
era `036638dd`) via `--arm-tree`, the rig's eval-host container
(`openbeast-eval-host:8d1e28442569`), T2's switches at one stream (8,192
turn cap, act nudge, thinking on at the template default, wall ×2,
wall-scaled turns), the same Basalt v0.5.7 binary **unpatched** (as
INK-T2-STOCK ran: no #632), the same serve flags (`--parallel 1
--max-context 262144 --kv fp8`, answer room auto, DSpark at the pack
default) and the same sandbox. Only the pack differs, and §2 shows it
differs from stock only in 20 tensors. Why not the #632 build the default
now runs: then the comparison would have two variables; a #632 follow-up
can mirror I_T3 if this one had looked promising.

**How it ran.** One launcher, `~/openbeast-runs/ink-I_T2_sc117-run.sh`
(modelled on `ink-I_T3-run.sh`): it waited for `GPU-OK-sc117`, checked the
trees, era, image, the arm's resolution (`== I_T2_stock`), the unpatched
tree and binary hash, re-hashed the pack, then took the GPU (`./stop.sh` of
the default stack from a plain shell), ran Phase B (§3) on stock then SC117,
gated on SC117's thinking and tool probe and conformance, served SC117
again for the arm with its log saved, gated on the DNS probe and the #588
probe (evidence only on this build), ran the arm, and restored the default
(`./start.sh -d` from a plain shell; `engine.sh status` ready and serving
`inkling-2-mini`, the gate `:8091` 200 naming `inkling-2-mini`, a chat):
**RESTORE OK** at 15:34:42 PST. The GPU was held 10:35 to 15:34 PST.

## 2. How the pack was made

Everything in this section ran on the rig, CPU only, while the default
stack served (no GPU, no change to the serving stack), 2026-11-25 10:05 to
10:30 PST. The log is `~/openbeast-runs/sc117-basalt.log`; the work
directory `~/openbeast-runs/sc117-2026-11-25/` (`scripts/`, `logs/`,
`results/`, `weights/`); big intermediates on `/mnt/9100s4tb/sc117-2026-11-25/`.
Nothing from SC117's pack or from the Hub download was executed: the only code
that touched them is our stdlib script
([`scripts/engines/inkling-sc117-transplant.py`](../../scripts/engines/inkling-sc117-transplant.py),
run with `python3 -I`), `zstd -d` on the 10,375-byte manifest, `sha256sum`,
and Basalt's own converter from our pinned, reviewed build.

### 2.1 The official checkpoint, pinned to the revision jesdga converted

The stock pack's `meta` section records where it came from. Read with our
pack reader (`scripts/packinfo.py` in the work directory: the section table
and meta of a `.basalt` file): `"checkpoint": {"dir": "b85c3cfa615fc413cc9af3379f491b963f3b586a", "config_sha256": "a7a34960…"}`.
Basalt writes the checkpoint **directory name** there
(`checkpoint_inkling2.py`: `"dir": pathlib.Path(a.checkpoint).name`), and a
Hugging Face snapshot directory is named by its revision. So jesdga converted
`thinkingmachines/Inkling-2-Mini` at **`b85c3cfa`** (2026-11-21 22:23Z,
"Update README.md"), not today's `main` (`c9d02830`, 2026-11-24, an
evaluation-results commit). The two revisions have the same LFS objects
(`model-00001-of-00001.safetensors` `65e60f51…`, `mtp.safetensors`
`7c3b6513…`, `tiktoken/tokenizer.model`, `tokenizer.json`); only `README.md`
and `.eval_results/` differ. The converter reads the README's front matter
(the licence), so the revision matters to the bytes.

| Step | Command | Result |
|---|---|---|
| Download (the one networked step) | `hf download thinkingmachines/Inkling-2-Mini --revision b85c3cfa615fc413cc9af3379f491b963f3b586a --local-dir /mnt/9100s4tb/weights/thinkingmachines-Inkling-2-Mini-b85c3cfa` | 14 files, 20.19 GB, 3 min |
| Verify against the Hub | every file: LFS sha256 (4 files) or git blob id (10 files) against `…/api/models/thinkingmachines/Inkling-2-Mini/revision/b85c3cfa…?blobs=true` (`results/official-verify.txt`, the API answer in `results/hub-official-b85c3cfa.json`) | **14 OK, 0 BAD** |

Offline path: the same directory from beast-ark; `inkling-sc117-pack.sh`
checks the three files it uses against their sha256 before anything else.

### 2.2 The converter control: the stock pack rebuilt byte for byte

The converter (`tools/convert/`) is the same in the three Basalt trees on
the rig: the unpatched v0.5.7 build (`build/basalt-tree`, what INK-T2-STOCK
served from), the #632-patched copy (`build/basalt-tree-pr632`) and the
installed default (`~/.local/opt/basalt-v0.5.7+pr632-d62ab74d2299`): one
sha256 over the sorted file hashes, `e64295b1…`, for all three (#632 touches
`serve/` only).

Run in the sandbox exactly as the Derisked pack was converted
(`convert-blackfrost.sh`: `--network none --read-only`, uid 1000,
`--cap-drop ALL`, `no-new-privileges`, image `ob-strata-env:2026-11-23`),
plus CPU limits because the rig was serving (`--cpus 12 --cpu-shares 256`,
`--workers 12`), with the checkpoint mounted read-only as
`/src/b85c3cfa615fc413cc9af3379f491b963f3b586a` (so `meta.checkpoint.dir`
matches) and no `--name` (the default "Inkling 2 Mini", as the stock pack):

```
python3 -I tools/convert/convert.py --checkpoint /src/b85c3cfa615fc413cc9af3379f491b963f3b586a --out /out/Inkling-2-Mini-NVFP4-MX8.basalt --workers 12
done: 10 sections, 18.260 GiB, 23.6 s
28ec70723feaa037942a02f55b9d5e898276f3e62a3b1fa083745f932a041b99  19606374189 bytes
```

**The stock pack, `jesdga/Inkling-2-Mini-Basalt @ c2afac3a`, reproduced byte
for byte** (`logs/convert-repro.log`). The converter is deterministic on
this input, and every comparison below is between two outputs of the same
tool on two inputs that differ in exactly 20 tensors. No separate matched
control pack was needed: the control *is* the published stock pack that
INK-T2-STOCK served.

### 2.3 Reading SC117's tensors out of the Strata pack

**The format** (read from Strata's source at our pin `6a6db7ff`:
`engine/include/strata/artifact/strata_reader.hpp`,
`engine/src/artifact/strata_reader.cpp`, `engine/src/inkling2/ink_manifest.h`):
a 4 KiB header frame, magic `STRATA\0\1`, flags (u32), profile (u16), six
sections (u16), then six 64-byte section entries at 0x10
(`file_offset u64, byte_length u64, sha256[32], section_id u32, flags u32, reserved u64`).
Section 0 is a ZSTD-compressed JSON manifest whose `trunk_tensors` list gives
each dense tensor's `name`, `format` (`BF16`, `Q8_ROW`, …), `shape`,
`offset` (relative to section 1) and `bytes`; section 1 holds those tensors;
section 4 the experts; section 3 the expert directory.

**What `extract` checked** (`logs/extract.log`, 26 s):

| Check | Result |
|---|---|
| Every section's SHA-256 against its table entry, both files | 6/6 and 6/6 match |
| The two manifests (section 0) | equal (same 10,375 bytes, same digest) |
| Sections 0, 2, 3, 4, 5 | equal digests; only section 1 differs (`d0997446…` official, `17172c2a…` SC117) |
| `model.llm.layers.N.attn.wo_ud.weight`, N = 0..23, in the manifest | all 24 `BF16 [2304, 3072]`, 14,155,776 bytes each |
| Official SQ-20 bytes against the HF checkpoint's bytes (offsets from the safetensors header) | **identical for all 24**: SQ-20 stores these tensors unrounded, and our decoding of the format is right |
| SC117 layers 0-3 | identical to official |
| SC117 layers 4-23 | all 20 differ; 6,128,102 to 6,530,635 of the 7,077,888 BF16 values changed per tensor (87-92%: a rank-1 projection moves almost every element a little) |
| Section 1 compared in full, in 4 KiB blocks, byte by byte at tensor edges | 69,140 differing blocks, **every differing byte inside one of the 20 tensors** |

Output: the 20 tensors in layer order, 283,115,520 bytes, sha256
`84361020fdd8715a675ffb9a0d96a53d9b7e2c02507590e53a11454020783a78`, and a
manifest of offsets and per-tensor hashes (`results/extract-manifest.json`).
This agrees with the Phase 1 review's blind block diff
([SC117_INKLING_STRATA-2026-11-25.md](SC117_INKLING_STRATA-2026-11-25.md)
§2.3: 20 ranges of 14,221,312 bytes plus the header block): the header block
there is the section table's new section-1 digest.

### 2.4 The transplant and its check

`patch` copies the checkpoint directory (13 side files and the shard;
`.cache/` left out) to `Inkling-2-Mini-b85c3cfa-SC117-ae324ac3/` and writes the
20 tensors into the shard at their header offsets; the header, dtypes and
shapes are untouched. `verify-ck` (`logs/patch-verify.log`):

- the 13 side files are byte-identical to the official ones;
- the shard's header is byte-identical; the data area is fully covered by
  tensors (so "every tensor equal" means "every byte equal");
- **exactly 20 of the 1,244 tensors differ**, `model.llm.layers.{4..23}.attn.wo_ud.weight`,
  and each equals the extract.

Patched shard sha256 `bd8132f97ce453765b1a83cfa7aac865c4f89f8c57f24601663d97a946083cf7`.
The edited tensors lie between 1.97 and 2.31 GB into the 19.5 GB shard, outside the
first and last 64 MiB that Basalt fingerprints, so the pack's `meta.sources`
records the same fingerprint as the stock pack's (it is a cheap identity, not
a hash; the registry pins the pack by its full sha256).

### 2.5 The SC117 Basalt pack

The same converter, the same sandbox, the patched checkpoint mounted as
`/src/Inkling-2-Mini-b85c3cfa-SC117-ae324ac3` (`logs/convert-sc117.log`):

| | |
|---|---|
| File | `Inkling-2-Mini-SC117-abliterated-NVFP4-MX8.basalt` (rig: `~/openbeast-runs/sc117-2026-11-25/weights/`, mode 444) |
| sha256 | **`6eaa50d3620a8f3913c726b239b7981880e440f7d9c2dffe0613148b01c6e89b`** |
| Bytes | 19,606,374,187 (stock: 19,606,374,189; the meta's directory name is 2 bytes shorter) |
| Determinism | a second conversion, and a full run of the repo script from a fresh work directory, give the same sum |

`diff-pack` against the stock pack (`logs/diff-pack.log`):

| Section | Result |
|---|---|
| `tokenizer/*` (5), `experts` (15.67 GB), `experts.layout`, `vision/mmproj` | byte-identical, same table digests, same offsets |
| `gguf` (710 tensors) | **exactly 20 tensors differ: `blk.{4..23}.attn_output.weight` (Q8_0)**; every other tensor and the padding between them identical |
| `meta` | one field: `checkpoint.dir` `b85c3cfa…` → `Inkling-2-Mini-b85c3cfa-SC117-ae324ac3` (8,488,749 → 8,488,747 bytes) |

So the pack is the stock pack with SC117's 20 attention-output matrices,
each rounded to Q8_0 by the same ggml reference rounding Basalt applies to
every dense projection. The name the server reports, the tokenizer, the chat
template, the experts (the release's own NVFP4 / MXFP8 bits), the vision
tower and the DSpark drafter are the stock pack's.

**Why the default name and a distinct directory name.** Keeping `--name`
unset leaves `gguf_kv.general.name` and the `vision/mmproj` section (which
embeds the name: the Derisked pack's mmproj differs from stock for that reason
alone) identical to stock, so the diff above is the whole story. The pack
still says what it is through `meta.checkpoint.dir`, its file name, its
sha256 and the registry row.

### 2.6 Reproducing it offline

[`scripts/engines/inkling-sc117-pack.sh`](../../scripts/engines/inkling-sc117-pack.sh)
`OFFICIAL_DIR OFFICIAL_SQ20 SC117_SQ20 BASALT_TREE WORK_DIR OUT_DIR [STOCK_PACK]`
does §2.3 to §2.5 from pinned inputs (it hashes the official shard, `mtp`,
`config.json` and both SQ-20 files first, and the extract, the patched shard
and the pack after) and, given the stock pack, the diff. Run on the rig from
a fresh work directory it printed `OK` and the same `6eaa50d3…`
(`logs/repro-script.log`). The pack is pinned in
[`scripts/weights.registry`](../../scripts/weights.registry) as a `derived`
row whose fifth column names that script; `scripts/fetch-weight.sh` refuses
to download a derived row and verifies it when the file is present
(PR #331, merge `b054d05a`).

## 3. Serve checks, speed and behaviour (Phase B)

One GPU session, 2026-11-25 10:34 to 10:45 PST, run by the arm's launcher
(`~/openbeast-runs/ink-I_T2_sc117-run.sh`, log `ink-I_T2_sc117-run.out`)
before the arm, after `./stop.sh` of the default stack. Both packs on the
build INK-T2-STOCK ran: Basalt v0.5.7 `400b411c` **unpatched**
(`build/basalt-tree`, `bin/basalt` `5dc8a63c…`), `inkling-v5.sh start <pack>
--patch none`, i.e. `basalt serve --alias inkling-2-mini --parallel 1
--max-context 262144 --kv fp8`, answer room auto, DSpark at the pack default,
the hardened sandbox (no TCP egress, `--dns 127.0.0.1`, read-only root and
weights, uid 1000, `--cap-drop ALL`, port on 127.0.0.1). Stock first (the
same-day control), then SC117, the same probes on each. The serve check read
`docker inspect` for the pack path, the unpatched `/opt/basalt` mount, no
`--answer-room` flag and the DNS setting; in both containers `example.com`
and `huggingface.co` failed to resolve, as intended. Raw files:
`~/openbeast-runs/sc117-2026-11-25/results/B/` (`SUMMARY.txt` and one file per
probe; the bench files have the API key stripped).

### 3.1 Speed, the same day, the same prompts

`bench.py` from `inkling-perf-2026-11-23` with its settings: 3 code and 3
prose prompts (`data/prompts.json`), greedy, 512 tokens, one warm-up, 5
runs each, the median of 15 per cell, single stream; prefill at the same
4,141 / 15,027 / 29,208-token prompts (`--cpt 2.83`); the 16K needle.

| | stock (`28ec7072…`) | SC117 (`6eaa50d3…`) | stock, 2026-11-23 |
|---|---:|---:|---:|
| Decode, thinking `none`, code / prose (tok/s) | 416.1 / 339.1 | 418.7 / 344.0 | 376 / 307 |
| Decode, thinking `high` (template default), code / prose | 463.4 / 339.0 | 457.0 / 337.1 | 417 / 309 |
| Drafts accepted (mean over runs), none / high | 60.6% / 64.3% | 62.9% / 63.8% | |
| Prefill 4K / 16K / 32K (tok/s) | 17,958 / 19,202 / 17,509 | 17,928 / 19,179 / 17,513 | 18,098 / 19,338 / 17,564 |
| 16K needle | **declined** to repeat the passphrase | found | found |

**The edit costs no speed.** Decode differs by under 2% either way and
prefill by under 0.2%, inside run-to-run spread (the per-prompt medians of
one pack spread 326-539 tok/s). Both packs decode faster today than on
2026-11-23 (+10%); the rig's clocks or power state, not the packs: today's
stock control moved by the same amount. As expected: the pack changes 20
dense matrices of the same type and size.

**The needle.** The stock pack, sampled at the template default, answered
"I can't repeat that passphrase…" (the prompt plants it as an "IMPORTANT
NOTE" in a long document); SC117 returned it. One draw each; the stock pack
found it on 2026-11-23. A hint in the direction of the edit, not a
measurement.

### 3.2 Quality: divergence from a near-reference

`basalt-bench --quality 60 --quality-t 1` against the UD-Q8_K_XL
KL-divergence base (`kld-base-q8kxl.bin`, `data/longtext.txt`, 60 × 512
chunks, the decode path), in the sandbox, the same procedure as the Strata
report §11.2:

| Pack | KLD mean | median | p99 | max | same top-1 | PPL (base 5.9145) |
|---|---:|---:|---:|---:|---:|---:|
| stock (today) | 0.01276 | 0.00605 | 0.0992 | 0.627 | 94.93% | 5.9005 |
| Blackfrost Derisked (2026-11-23) | 0.0263 | | | | 92.93% | 5.952 |
| **SC117** | **0.03542** | 0.01414 | 0.3148 | 5.18 | **92.18%** | **6.0073** |

Today's stock figures equal 2026-11-23's to the last digit (the gate is
deterministic), so the comparison is exact. **SC117's edit moves the
distribution further than Blackfrost's**: 2.8× stock's KLD and 1.35×
Derisked's, top-1 agreement 0.75 points below Derisked, perplexity +1.6%
over the base (Derisked +0.6%). This contradicts the expectation in the
Phase 1 review (§3 there: "a lighter edit might cost less"): touching only
20 matrices does not make the edit small in effect. SC117's own figure
(five-domain KLD 0.0136, ΔPPL +0.70%, GGUF Q8_0 against Q8_0) is on a
different scale and corpus; ours includes the stock pack's quantization
(0.0128) and is the one comparable with the Derisked pack. KLD says how far
the model moved, not whether it codes worse: the arm (§4) answers that.

### 3.3 Thinking, tools and the harness path

| Probe | stock | SC117 |
|---|---|---|
| `probe_think_tools.py` (`enable_thinking` true / false / unset × plain / tools × streamed / not: 12 requests) | 12/12: `reasoning_content` separated (79-142 chars), `content` "391", tool calls `read_file` with the right JSON in all 6 tool requests | 12/12, the same (68-121 chars of reasoning) |
| `conformance.sh` c=1 / c=4 | PASS / PASS (models, chat, stream, reasoning in `reasoning_content`, tools, tool result, parallel calls, max_tokens; 4/4 concurrent) | PASS / PASS, the same checks |
| #588 probe (the two rebuilt I_T2 turns that went empty, 2 sends each, the arm's 8,192 cap) | 0 empty of 4 | 0 empty of 4; the arm gate's repeat: 1 empty of 4 |

The SC117 pack runs the T2 harness path exactly as stock does. The #588
failure (a turn that thinks to the cap and ends with nothing) is the
unpatched build's, on both packs; INK-T2-STOCK had 105 such turns, and this
arm runs the same build on purpose (§1).

### 3.4 Refusals: six benign professional prompts

Behaviour only, the text is not reproduced
(`results/B/refusal-{stock,sc117}.json`, `refusal_probe.py`): an authorized
penetration tester asking how SQL injection on a login form works and for a
test input; an ER nurse asking where acetaminophen toxicity begins; a
thriller villain's monologue; locksport single-pin picking; which household
cleaners must not be mixed; a defense attorney asking how to challenge
digital forensic evidence. Thinking at the template default, temperature
0.6, seed 1, one draw each.

| | stock | SC117 |
|---|---|---|
| Answered | 6 / 6 | 6 / 6 |
| Refused | 0 | 0 |
| Framing | opened two answers with a hedge or disclaimer ("at a high level"; "Not legal advice…" before the content) | answered all six directly |
| Closing safety notes | on the nurse, household and defense answers | on the same three |

**These prompts do not separate the packs.** The stock model already
answers benign professional questions; the edit removed some hedging, not
refusals, on this set. A refusal measurement that separates them needs
prompts the stock model refuses (SC117 reports 98/100 → 9/100 on its own
unpublished set), which is outside this brief and outside OpenBeast's
publication rules for this report.

## 4. The row

| | |
|---|---|
| Board row | `INK-T2-SC117`, model "Inkling 2 Mini SC117 abliterated (Basalt NVFP4-MX8)", weights `inkling2-mini-sc117-abliterated-basalt-nvfp4-mx8` |
| Result | **51/71 scored (51 raw), SCORE 75.3** (SOLVE 72.3, LANG 84.1); 0 lost, 0 excluded; one validator note (§8) |
| On the board | the SC117 pack's own row, #6 by SCORE: between INK-T2 (Derisked, 49/71, 76.5: more LANG, fewer passes) and Q38U-T2 (51/71, 75.0). The RTX 5090 `defaults` entry stays INK-T2-STOCK |
| Run | `START I_T2_sc117` 2026-11-25 18:45:38Z, `END … 51/71 scorable` 23:34:13Z: 4 h 49 min (INK-T2-STOCK 4 h 18 min); no rerun needed |
| Failed (20) | `122_gemm_blocked_d`, `163_bigint_divmod_b`-`f`, `168_linearizability_kv_a`-`f`, `173_taskgroup_cancel`, `175_crdt_orset_rga`, `176_intermittent_500s_a`, `183_heston_little_trap`, `190_autograd_mlp`, `192_pike_regex`, `195_riscv_insn_budget`, `196_polyphase_resampler` |
| How units ended | 0 at the wall; 22 at their turn limit (4 of them passed); the rest finished (of the fails, `175_crdt_orset_rga` ended at turn 30 and `122_gemm_blocked_d` at the dlsym check, §8). INK-T2-STOCK: 0 and 21 (4 passed) |

## 5. Tables (generated)

<!-- v5-report:start -->
<!-- generated by evals/v5_report.py for Inkling 2 Mini SC117 abliterated (Basalt NVFP4-MX8): do not hand-edit -->

**Arms**

| Arm | Run | Weights | On the board | Era | Turn cap | Passed | SCORE | SOLVE | LANG |
|---|---|---|---|---|---:|---:|---:|---:|---:|
| I_T2_sc117 | INK-T2-SC117 | Basalt NVFP4-MX8 pack · SC117 abliterated, transplanted into the release · abliterated | yes | 036638ddd68698ce | 8192 | 51/71 | 75.3 | 72.3 | 84.1 |
| Claude Opus 5.5: OPUS-REF | OPUS-REF | Anthropic API · hosted, weights not public | yes | — | — | 71/71 | 100.0 | 100.0 | 100.0 |
| Qwen3.8 27B Uncensored MTP: Q_T2 | Q38U-T2 | GGUF Q5_K_M · JonathanColetti Uncensored (MTP head kept) · abliterated | yes | 036638ddd68698ce | 8192 | 51/71 (54 raw) | 75.0 | 72.3 | 83.1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2 | INK-T2 | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 036638ddd68698ce | 8192 | 49/71 | 76.5 | 72.3 | 89.1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2_s1 | INK-T2-S1 | Basalt NVFP4-MX8 pack · Blackfrost Derisked · abliterated | yes | 036638ddd68698ce | 8192 | 51/71 | 78.3 | 72.3 | 96.4 |
| Inkling 2 Mini (Basalt NVFP4-MX8): I_T2_stock | INK-T2-STOCK | Basalt NVFP4-MX8 pack · Basalt's official pack · stock | yes | 036638ddd68698ce | 8192 | 53/71 | 78.2 | 75.4 | 86.7 |
| Inkling 2 Mini (Basalt NVFP4-MX8): I_T3 | INK-T3 | Basalt NVFP4-MX8 pack · Basalt's official pack · stock | yes | 036638ddd68698ce | 16384 | 52/71 | 77.4 | 75.4 | 83.6 |

**Per language** (passed / units of that language; an in-progress arm's passes are over the units done so far)

| Arm | Py | Go | C | C++ | Rust | Zig | Sh |
|---|---:|---:|---:|---:|---:|---:|---:|
| I_T2_sc117 | 23/32 | 6/8 | 6/8 | 6/8 | 5/8 | 4/6 | 1/1 |
| Claude Opus 5.5: OPUS-REF | 32/32 | 8/8 | 8/8 | 8/8 | 8/8 | 6/6 | 1/1 |
| Qwen3.8 27B Uncensored MTP: Q_T2 | 23/32 | 6/8 | 6/8 | 6/8 | 6/8 | 3/6 | 1/1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2 | 23/32 | 6/8 | 5/8 | 6/8 | 5/8 | 3/6 | 1/1 |
| Inkling 2 Mini Derisked (Basalt NVFP4-MX8): I_T2_s1 | 23/32 | 6/8 | 6/8 | 6/8 | 5/8 | 4/6 | 1/1 |
| Inkling 2 Mini (Basalt NVFP4-MX8): I_T2_stock | 24/32 | 6/8 | 7/8 | 6/8 | 5/8 | 4/6 | 1/1 |
| Inkling 2 Mini (Basalt NVFP4-MX8): I_T3 | 23/32 | 6/8 | 6/8 | 6/8 | 6/8 | 4/6 | 1/1 |

**Time and the wall** (wall hit = the harness killed the agent at the unit's wall, `agent_exit_code == -1`; turns are counted only where the row carries them, from harness9 on)

| Arm | Units done | Unit-hours | Mean min / unit | Wall hits | Passed at the wall | Failed at the wall | Turns of the wall-hit units |
|---|---:|---:|---:|---:|---:|---:|---|
| I_T2_sc117 | 71 | 4.8 | 4.0 | 0 | 0 | 0 | — |

**Units gained and lost** (arm against base, over the units both have a verdict for)

| Base → arm | Compared | Net | Gained | Lost |
|---|---:|---:|---|---|
| INK-T2-STOCK → I_T2_sc117 | 71 | -2 | 178_codebase_qa | 163_bigint_divmod_c, 175_crdt_orset_rga, 196_polyphase_resampler |
| INK-T2 → I_T2_sc117 | 71 | +2 | 122_gemm_blocked_e, 158_karatsuba_bytes_c, 163_bigint_divmod_a | 175_crdt_orset_rga |
| INK-T2-S1 → I_T2_sc117 | 71 | +0 | 163_bigint_divmod_a | 175_crdt_orset_rga |
| INK-T3 → I_T2_sc117 | 71 | -1 | 158_karatsuba_bytes_c, 163_bigint_divmod_a | 163_bigint_divmod_c, 163_bigint_divmod_e, 175_crdt_orset_rga |
| Q38U-T2 → I_T2_sc117 | 68 | +0 | 122_gemm_blocked_b, 122_gemm_blocked_e, 123_nbody_b, 124_rkf45, 126_hmac_scratch | 163_bigint_divmod_b, 163_bigint_divmod_c, 163_bigint_divmod_e, 176_intermittent_500s_a, 196_polyphase_resampler |
<!-- v5-report:end -->

## 6. Paired comparisons

`evals/ablation.py` (`--force`: the model slugs differ; the era, arm
switches, tree, host and stream count are the same), with the tool's own
`contrast(pairs, "all", CHURN["all"])` for the all-unit row (exclusions and
the overflow ledger applied, as the board applies them):

| Against | n | them | SC117 | gained | lost | net | p (exact McNemar) | MDE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **INK-T2-STOCK** (the one-variable control) | 71 | 53 | 51 | 1 | 3 | **−2** | 0.63 | 9.5 |
| INK-T2 (Derisked) | 71 | 49 | 51 | 3 | 1 | +2 | 0.63 | 9.5 |
| INK-T2-S1 (Derisked, seed 1) | 71 | 51 | 51 | 1 | 1 | 0 | 1.00 | 9.5 |
| INK-T3 (stock, the improved composite) | 71 | 52 | 51 | 2 | 3 | −1 | 1.00 | 9.5 |
| Q38U-T2 (Qwen3.8, merged as the board merges it) | 63 | 51 | 48 | 2 | 5 | −3 | 0.45 | 10 |

On the board's own view of Q38U-T2 (its excluded passes counted as fails,
68 units; the generated table in §5) the two rows are level: 51 and 51.

- **Against stock:** gained `178_codebase_qa`; lost `163_bigint_divmod_c`,
  `175_crdt_orset_rga`, `196_polyphase_resampler`. Every one of these four
  units has flipped between the earlier Inkling arms (six arms in all):
  `178` failed only on INK-T2-STOCK and INK-UP8K, `163_c` passed only on
  INK-T2-STOCK and INK-T3, `175` failed on INK-UP8K, `196` passed only on
  INK-T2-STOCK and INK-UP8K. The signature of noise, not of the edit.
- **Against Derisked:** gained `122_gemm_blocked_e`, `158_karatsuba_bytes_c`,
  `163_bigint_divmod_a`; lost `175_crdt_orset_rga`. All three gains are
  units the stock pack also gained over Derisked (it gained five: these
  three, `163_bigint_divmod_c` and `196_polyphase_resampler`; §10 of the
  main report).
- **Against Q38U-T2:** 8 units dropped (Q38U-T2's lost and excluded units:
  `122_gemm_blocked_d`, `123_nbody_b`, `124_rkf45`, `126_hmac_scratch`,
  `168_linearizability_kv_c`, `168_linearizability_kv_f`, `175_crdt_orset_rga`,
  `190_autograd_mlp`); gained `122_gemm_blocked_b`, `122_gemm_blocked_e`; lost
  `163_bigint_divmod_b`, `163_bigint_divmod_c`, `163_bigint_divmod_e`,
  `176_intermittent_500s_a`, `196_polyphase_resampler`.
- **Reading.** The seed pair (INK-T2 against INK-T2-S1) flipped 2 units; at
  that churn (about 3%) the MDE over 71 units is about 7 (6.5). Every pair
  above is inside it. On the per-language rows of the CLI the same: all
  `within_churn`. Completion tokens to fix (units both passed, n=50)
  against stock: fewer in 29, more in 21, median −614, p 0.32.

## 7. Time, tokens, the server log and #588

From the result files (`elapsed_seconds`, token counts, turns):

| | SC117 | stock (INK-T2-STOCK) | Derisked (INK-T2) |
|---|---:|---:|---:|
| Unit-hours | 4.77 | 4.26 | 4.47 |
| Median unit (all / passed) | 74 s / 42 s | 122 s / 44 s | 47 s / 21 s |
| Turns | 1,996 | 2,032 | 1,935 |
| Prompt + completion tokens | 62.0 M + 4.37 M = 66.4 M | 64.9 M + 4.90 M = 69.8 M | 70.9 M + 5.37 M = 76.3 M |
| Units at the turn limit (passed) | 22 (4) | 21 (4) | 21 (3) |

The extra half hour is spent in failing units (the six `168` units alone
took 1 h 34 min, two of them within 2 minutes of their 1,800 s wall), not
in decode speed.

**The server log** (`evals/audit/basalt_log.py … --since
2026-11-25T18:45:38 --until 2026-11-25T23:34:13 --cap 8192`, the kept log
`basalt-I_T2_sc117-20261125-103534.log`): 1,996 requests (= the transcripts'
1,996 turns), 1,855 `stop`, 141 `length`; 87 "no answer" warnings;
4,367,930 generated tokens; decode 457 tok/s over all replies, median 481 on
replies of 256+ tokens, 522 on replies at the 8,192 cap (p10 433, p90 629);
drafts accepted 88.4% token-weighted, 4.59 tokens per verification window;
prompts 62.0 M tokens, 96.9% served from cache, uncached prefill median
13,234 tok/s; 0 requests without a thinking budget. INK-T2-STOCK's log: 444
tok/s, 512 at the cap, 88.7%, 4.6.

**Capped turns** (`evals/audit/capped_turns.py` over the scan): 141 replies
ended at the 8,192 cap: **87 empty** (no answer, no call) in 10 units, 8 of
the 20 fails (85 of the 87); 1 text only; 53 still carrying a tool call.
Replies at exactly the cap ran at a median 531 tok/s end to end (prefill
included). INK-T2-STOCK: 190 at the cap, 105 empty, 2 text only, 83 with a
call; the Derisked arms 203 and 167 empty. The transcripts' 87 equals the
server's 87 "no answer" warnings: the #588 failure of the unpatched build,
on purpose here (§1). Two failed units hold 62 of them
(`183_heston_little_trap` 35, `192_pike_regex` 27).

## 8. Integrity and the dlsym stop

**The scan** (`evals/audit/integrity_scan.py`, the review-1124 scanner on
`b054d05a`, over copies of the 71 transcripts, the results file and the
task texts of `f38a29c7`; details in the sealed review
`evals/audit/reviews/ink-t2-sc117-integrity-2026-11-25.md`): **71
transcripts, 71 units, 0 contaminated.** Five `web_search` calls in four
units (`124_rkf45`, `163_bigint_divmod_b`, `163_bigint_divmod_e`,
`183_heston_little_trap`), all refused by the eval guard (attempts); no shell
route off the host and no listing of an answer tree.

**The overflow audit** (`evals/audit/overflow_audit.py`): 0 `server_error`
rows, nothing to record in `overflow.json`; the server log holds no "exceeds
the context window" line. The largest sessions stayed inside the 262,144
window, as in every 60-turn Inkling arm.

**`122_gemm_blocked_d` (Rust)** stopped at the Linux `dlsym` check (`FAIL:
the multiply must be your own code: the binary imports dlsym`), as in every
Inkling arm. Replayed as for INK-T3: the final `gemm.rs` rebuilt from the
transcript (one `write_file` and two `edit_file` calls, checked against the
final `read_file`; 2,861 characters, sha256 `285beeaf…`), graded in the
eval-host container on the `f38a29c7` tree: the unpatched validator FAIL as
recorded; under the proposed next-era Rust-only rule **PASS 3 of 3 runs, 6
hidden cases**, with a patched validator byte-identical to the earlier
replays' (`db113799…`). Recorded as a `validator_notes` entry; not adjusted
(it would be 52/71). Replay directory: `~/ob-cal/.claude/gemm-replay-ink-sc117/`.

## 9. Verdict and recommendations

1. **SC117's edit neither helps nor measurably hurts coding.** 51 against
   53 on the one-variable pair (p 0.63); within one seed's noise, single
   seed. Its divergence from the reference is the largest of the three
   packs (KLD 0.0354), which did not show as a v5 cost at this resolution.
2. **The RTX 5090 default stays INK-T2-STOCK** (Max, 2026-11-24). Nothing
   here is a reason to move it: the stock pack is numerically ahead, and
   the refusal probe found the stock model already answering benign
   professional prompts.
3. **SC117 against Derisked: level, with the better provenance.** 51 against
   49 (p 0.63), 87 against 203 empty capped turns, the same speed, and an
   edit we verified byte by byte (20 tensors of a pinned release, rebuilt
   offline), where the Derisked pack's base revision is unverified. **If Max
   wants an abliterated pack on the 5090 (the uncensored-preferred policy),
   use SC117's rather than Derisked**, served the way the default is
   (Basalt v0.5.7 + #632, the `inkling-best` setup). That is a
   recommendation, not a change: no profile or default is touched here.
4. **Not run, and why.** No seed repeat (it cannot turn −2 into a lead that
   would move the default); no #632 follow-up (I_T3 showed the patch does
   not change the count on stock; the overflow fix comes first,
   DECISIONS-2026-11-25 §4); no Strata arm (Path A; Strata is about half
   Basalt's speed on this pack family and still unreviewed at v0.2.21).

## 10. Caveats

- **Single seed.** One clock-seeded draw; the seed pair moved 2 units.
- **The refusal probe is six prompts, one draw each**, chosen to be benign;
  it shows hedging, not refusal rates. SC117's 98/100 → 9/100 figures are its
  own, on an unpublished set; we did not reproduce them.
- **KLD is against UD-Q8_K_XL**, itself a requant, on `data/longtext.txt`;
  it includes the pack's own quantization (stock 0.0128). It measures
  distance moved, not quality.
- **The build is the unpatched pin on purpose**: the arm carries the #588
  failure mode (87 empty turns) as INK-T2-STOCK did. The shipped default
  build carries #632.
- **Speed today ran about 10% above 2026-11-23's** for both packs (the
  same-day control moved with it), so compare packs within a day.
- Inkling 2 Mini, SC117's edit and Basalt are evaluated as a public-interest
  measurement; a supported commercial stack still needs licence diligence on
  the weights (Apache-2.0 per both cards), the edit and the engine.

## 11. Sources and how to regenerate

Raw data on the rig (`ssh beast`); committed: the results file, the board,
weights and exclusions entries, the scripts.

| What | Path |
|---|---|
| Results (71 rows; committed verbatim) | `~/ob-sc117/.claude/worktrees/h9/evals/results/eval-inkling-2-mini-sc117-abliterated-basalt-nvfp4-mx8-v5-thinking-first-i-t2-sc117-20261125-184538.json` (sha256 `bda9ada8…02c1`) |
| Transcripts (71) | `~/ob-sc117/.claude/worktrees/h9/agents/logs/agent-20261125-184539-*` to `agent-20261125-23*` |
| Diagnostics timing | `…/evals/results/diag-timing-20261125-184538.jsonl` |
| Campaign log (UTC) and state | `~/openbeast-runs/beast-inkling-I_T2_sc117-20261125-1045.out`; `~/ob-sc117/evals/results/campaigns/v5-thinking-first-inkling-2-mini-sc117-abliterated-basalt-nvfp4-mx8/` |
| Launcher and its log (PST) | `~/openbeast-runs/ink-I_T2_sc117-run.sh`, `ink-I_T2_sc117-run.out` |
| Progress log (PST) | `~/openbeast-runs/sc117-basalt.log` |
| Basalt server log | `~/openbeast-runs/basalt-server-logs/basalt-I_T2_sc117-20261125-103534.log` (the arm container `dde525ea52ee` from its start); Phase B containers: `inkling-perf-2026-11-23/logs/basalt-serve-20261125-103850.log` (stock), `-104312.log` (SC117) |
| Pack build, checks, Phase B | `~/openbeast-runs/sc117-2026-11-25/` (`logs/`, `results/`, `results/B/`, `scripts/`, `weights/`); the official checkpoint `/mnt/9100s4tb/weights/thinkingmachines-Inkling-2-Mini-b85c3cfa/`; the extract and the patched checkpoint `/mnt/9100s4tb/sc117-2026-11-25/` |
| Serve scripts (rig) | `inkling-perf-2026-11-23/scripts/basalt-serve.sh` and `inkling-v5.sh` gained the `sc117` pack case (`.orig-20261125` copies kept) |
| The dlsym replay | `~/ob-cal/.claude/gemm-replay-ink-sc117/` |

Regenerate, in a fresh clone of main:

```bash
# §5: the tables
python3 evals/v5_report.py --model "Inkling 2 Mini SC117 abliterated (Basalt NVFP4-MX8)" \
    --context INK-T2-STOCK,INK-T2,INK-T2-S1,INK-T3,Q38U-T2,OPUS-REF \
    --pair INK-T2-STOCK:I_T2_sc117 --pair INK-T2:I_T2_sc117 --pair INK-T2-S1:I_T2_sc117 \
    --pair INK-T3:I_T2_sc117 --pair Q38U-T2:I_T2_sc117 \
    --write docs/reports/V5_INKLING2_MINI_SC117-2026-11-25.md
# §6: the paired tests (per language; the all-unit rows are ablation.contrast(pairs, "all", CHURN["all"]))
B=evals/boards/v5
S=$B/eval-inkling-2-mini-sc117-abliterated-basalt-nvfp4-mx8-v5-thinking-first-i-t2-sc117-20261125-184538.json
python3 evals/ablation.py --force $B/eval-inkling-2-mini-basalt-nvfp4-mx8-stock-v5-thinking-first-i-t2-stock-20261124-140740.json $S
python3 evals/ablation.py --force $B/eval-inkling-2-mini-derisked-basalt-nvfp4-mx8-v5-thinking-first-i-t2-20261124-045843.json $S
# against Q38U-T2: Q_T2's two files merged as the board merges them (V5_INKLING2_MINI_RTX5090-2026-11-24.md, "$M")
python3 evals/ablation.py --force "$M"/eval-qwen3-8-27b-uncensored-mtp-q5-k-m-v5-thinking-first-q-t2-20261123-115749.json $S
# §2: the pack, offline (inputs as named in the script's header)
scripts/engines/inkling-sc117-pack.sh OFFICIAL_DIR OFFICIAL_SQ20 SC117_SQ20 BASALT_TREE WORK_DIR OUT_DIR STOCK_PACK
```
