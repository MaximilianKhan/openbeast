# SC117 Inkling 2 Mini abliterated (Strata packs): review and Phase 2 plan (2026-11-25)

> **Phase 2 path B done (2026-11-25): see [V5_INKLING2_MINI_SC117-2026-11-25.md](V5_INKLING2_MINI_SC117-2026-11-25.md). The transplanted Basalt pack ran arm `I_T2_sc117` at 51/71 (row INK-T2-SC117), level with INK-T2-STOCK and with Derisked; its KLD (0.0354) is larger than Derisked's, against §3's expectation.**
>
> **Status: Phase 1 done: read-only review, no GPU, no container.** Max
> (2026-11-25) downloaded `SC117/Inkling-2-Mini-abliterated-Strata` to the
> RTX 5090 rig and asked us to test the largest size with our optimizations
> (Strata or Basalt) and see whether it beats the Derisked pack. Max's
> condition, added mid-review: **only worth it if Inkling runs with thinking
> on under Strata.** That question is answered first (§1). Everything below was
> read from the pack, the Hub API, `gh api` on `Niko1221/Strata`, our repo at
> `origin/main` (`be7e0a2c`), or the rig's files on 2026-11-25 between 09:30
> and 10:40 PST. Nothing from the pack was executed. Scratch files are on the
> rig in `/tmp/sc117-review/` (my scripts and their outputs; not the pack's).

## 0. Verdict

- **Thinking under Strata: yes, now, in source. Not yet measured by us.**
  Strata v0.2.21 (2026-11-24, `d46f5f26`) passes Inkling's thinking levels
  through and returns `reasoning_content` (§1). Our pin `6a6db7ff` predates it,
  so this is new evidence and Max's condition is met on paper. The full review
  therefore went ahead.
- **The weights are worth benchmarking.** They are a small, well-documented
  edit, and we checked the claim ourselves. The abliterated SQ-20 is
  byte-identical to the official `strata-engine/Inkling-2-Mini-Strata` SQ-20
  except for the header block and 20 ranges that are exactly the size of a BF16
  `[2304, 3072]` `attn.wo_ud` tensor (§2.3). The experts, the drafter, the
  router and the vision shard are untouched. All checksums pass.
- **Strata is the wrong engine for maximum tok/s.** At our pin the same SQ-20
  pack ran at 201 / 131 tok/s (code / prose). Basalt's stock pack runs at
  376 / 307. v0.2.21 makes no RTX 5090 single-stream decode claim. Its 5090
  prefill claim for SQ-20 (9,019 tok/s) is half of Basalt's measured 18-19K
  (§3). Strata also has open blockers for a v5 arm: an uncapped second
  thinking block (#7084, the same failure as Basalt #588), wedges above ~65K
  tokens (#7034) while T2 needs 262,144, overflow wording the runner does not
  match (§5.4), and about 276 unreviewed commits since our pin.
- **Recommendation: serve these weights on Basalt.** Transplant the 20
  abliterated BF16 tensors into the official release checkpoint and convert
  it with Basalt's own converter. That gives an abliterated Basalt pack at
  Basalt's speed, with our #632 patch, DSpark and the T2 harness path. It is
  also the cleanest comparison available: arm `I_T2_sc117` against
  `INK-T2-STOCK` differs only in 20 tensors (§5.2). The Strata serve the
  brief asked for stays in the plan as a bounded speed and thinking check
  (§5.1), with no v5 arm on Strata unless it beats Basalt single-stream.

## 1. Thinking on Strata (Max's gate)

| Where | At our pin `6a6db7ff` (2026-11-23 09:17Z) | At v0.2.21 `d46f5f26` / main `2af30cca` (2026-11-24) |
|---|---|---|
| `terminal/serve/inkling_dialect.py` | `normalize_effort()` always `"none"`; `template_kwargs()` always `enable_thinking=False` (Strata report §12) | `EFFORT_LEVEL = {"none": 0.0, "minimal": 0.1, "low": 0.2, "medium": 0.7, "high": 0.9, "xhigh"/"max": 0.99}`; `template_kwargs()` sets `reasoning_effort` to the request's level and `enable_thinking = level > 0` (main, lines 404-457) |
| Default level | none | `STRATA_INKLING_EFFORT`, default **`low`** (0.2). The template's own default is 0.9 (`chat_template.jinja` line 5), which is what Basalt and T2 run |
| Reasoning returned | no (0 reasoning in 12 probes, Strata report §11.2) | commit `39b8407c` (2026-11-23 12:52Z, "serve: Inkling thinking levels…"): "The thinking is returned: message.reasoning_content (stream delta reasoning_content), Anthropic thinking blocks, Responses reasoning items, and usage…reasoning_tokens"; new tests `test_inkling_reasoning_fields.py`, `test_inkling_thinking_default.py` |
| Thinking budget | n/a | by level 1024/1024/2048/4096/8192/16384/16384 tokens (`STRATA_INKLING_THINKING_BUDGET` or `reasoning_budget_tokens` override); on reaching it the thinking is closed and the answer asked for (`c62ddc6e`, same day) |
| `terminal/strata_control/inkling_family.py` | `HAS_THINKING = False`, `REASONING_EFFORTS = ("none",)` | **unchanged** (lines 51-53). `gh api search/code?q=HAS_THINKING+repo:Niko1221/Strata` finds only the definition, so it is stale launcher/UI metadata, not the serve path |
| In a release | — | `gh api repos/Niko1221/Strata/compare/39b8407c43...v0.2.21`: `ahead`, behind 0, so v0.2.21 contains it; pin to v0.2.21 is 276 commits |

Open issues that matter (`gh search issues --repo Niko1221/Strata --state open`, 2026-11-25):

- **#7084** (2026-11-24, 0.1.67): after the thinking-budget wrap-up the model
  opens a second thinking turn that nothing caps; it runs to `max_tokens`
  and the reply is empty. This is Basalt #588 on Strata. INK-T2-STOCK ran
  with the Basalt equivalent unpatched (105 empty capped turns), so it does
  not rule Strata out, but it will cost units.
- #6947 (0.1.65): per-request `reasoning_effort` never reaches the template.
  Reported against 0.1.65, before the fix. Re-test it on 0.1.67.
- #6682: the reasoning channel is dropped when the budget ends inside it.
- #7034 (0.1.67, RTX 3090): prompts above ~65K tokens wedge the engine.

**The pack's own setting turns thinking off.** `configs/shared-settings.example.json`
is `{"reasoning_effort": "none", "temperature": 0.7, "top_p": 0.95, "top_k": 20}`.
Every SC117 measurement was taken on strata 0.2.19 / strata-inkling 0.1.65
(`results/verification.json` `engine`), and at that version thinking was
forced off (`5d84abe0`, 2026-11-22 "inkling2: enforce no-thinking mode (off
only)"). So none of the pack's numbers say anything about thinking.

**Conclusion.** Thinking with `reasoning_content` exists at v0.2.21 by source
and tests. It is unmeasured by us, and we have not checked tool calls with
thinking on. Phase 2's first GPU step (§5.1) is the same
`probe_think_tools.py` run that failed at the pin. If it shows no reasoning,
or tool calls break with thinking on, Max's rule stops the Strata path, and
the Basalt path (§5.2) does not depend on it.

## 2. Provenance and checksums

### 2.1 The repo

| Fact | Evidence |
|---|---|
| Repo `SC117/Inkling-2-Mini-abliterated-Strata`, revision **`ae324ac3676b97a3f67b475ffc2534c7521ff7c4`** | every `.cache/huggingface/download/*.metadata` line 1 on the rig; Hub API `sha` (`curl -s https://huggingface.co/api/models/SC117/Inkling-2-Mini-abliterated-Strata?blobs=true`) is the same |
| Created 2026-11-22 16:14Z; weights uploaded 16:14-16:18Z; last commit 23:09Z "Restyle bilingual READMEs…" | `…/api/models/SC117/Inkling-2-Mini-abliterated-Strata/commits/main` (7 commits) |
| Public, not gated, 821 downloads, 36 likes | API |
| Licence Apache-2.0 (card), inherited from `thinkingmachines/Inkling-2-Mini` (Apache-2.0 on its card) | `cardData.license`; `…/api/models/thinkingmachines/Inkling-2-Mini` |
| Author **SC117** ("Scorpio117"), HF user since 2026-04-25, 47 models, 843 followers, not an org | `…/api/users/SC117/overview` |
| SC117's line of work: abliterated / heretic / uncensored quants (GGUF, APEX, FIT tiers) of Qwen3.x, Gemma 4, Ornith, LFM, Ling and others; the top one has 4.05 M downloads | `…/api/models?author=SC117&limit=100` |
| Companion GGUF of the same edit: `SC117/Inkling-2-Mini-abliterated-FIT-GGUF` @ `12b8f126` (BF16 52.2 GB, Q6_K 23.5 GB, IQ2/IQ3 FIT tiers, mmproj) | API |

### 2.2 What it is and how it was made (its README, checked in §2.3)

- **Base:** `thinkingmachines/Inkling-2-Mini` (26.4B total, 128-expert MoE,
  looped transformer; `config.json` `inkling2_loop`). No base revision is
  given. The edit was computed on the HF weights (the KLD line is "Q8_0 vs
  Q8_0", i.e. GGUF) and then transplanted into the official Strata packs.
- **Method:** "rank-1 orthogonal subspace projection (`late_24_47_orth, k=1`)
  applied exclusively to `layers.4..23.attn.wo_ud.weight` (`s_nonloop=4.0`,
  `s_loop=2.5`)". That is 20 of 1,244 tensors. Refusal-direction
  abliteration on the attention output projection only.
- **Packaging:** "constructed directly from the official
  strata-engine/Inkling-2-Mini-Strata checkpoints by transplanting only the
  20 abliterated attention-output tensors … and recomputing the Section 1
  SHA-256 header digest".
- **Largest size: SQ-20** (19,346,685,952 bytes; `wo_ud` 24x BF16; the
  experts are the release's native SF4+MX8 bits, "lossless"). SQ-15
  (14.3 GB, 20 of 24 `wo_ud` at Q8_ROW) and SQ-10 (10.5 GB, all Q8_ROW)
  are smaller and lossy. `Inkling-2-Mini-Vision.strata` is a shared
  vision shard, not a size.

### 2.3 Verified on the rig (reads only)

| Check | Command | Result |
|---|---|---|
| Pack checksums | `cd /mnt/9100s4tb/weights/SC117-Inkling-2-Mini-abliterated-Strata && nice -n19 ionice -c3 sha256sum -c SHA256SUMS.txt` (`/tmp/sc117-review/sha_check.txt`) | **5/5 OK** (SQ-20 `4633e042…`, SQ-15 `b52a4827…`, SQ-10 `876a4586…`, Vision `6589eff4…`, tokenizer.json `7f332727…`) |
| Against the Hub | API `siblings[].lfs.sha256` | the same 5 values; also the same as `results/verification.json` and `.cache/huggingface/download/*.metadata` line 2 |
| Small files against the Hub | `git hash-object` on local copies vs API `blobId` | **15/15 equal** (README x2, configs x4, verification.json, chat_template x2, config.json, tokenizer files, .gitattributes) |
| Side files vs the official pack | `sha256sum` of chat_template.jinja, config.json, special_tokens_map.json, tokenizer.json, tokenizer_config.json against `~/openbeast-runs/inkling-perf-2026-11-23/weights/strata-Inkling-2-Mini-Strata-04996a1e/` | **identical**. The chat template is the official one (`53a9035f…`) |
| Vision shard vs official | Hub `lfs.sha256` of `strata-engine/Inkling-2-Mini-Strata` @ `04996a1e` | identical (`6589eff4…`) |
| **The transplant claim** | `python3 -I /tmp/sc117-review/blockdiff.py <official SQ-20 0bbfd523…> <abliterated SQ-20>` (64 KiB blocks; `/tmp/sc117-review/blockdiff-sq20.txt`) | same size; **21 differing ranges**: block 0 (the header digest) plus **20 ranges of 14,221,312 bytes** between offsets 1,056,571,392 and 2,337,931,264. One BF16 `[2304, 3072]` tensor is 14,155,776 bytes = 216.0 blocks, which spans 217 blocks (14,221,312) when it is not block-aligned. **Everything else, 17.0 GB including all experts and the drafter, is byte-identical to the official pack** |
| `wo_ud` dtype in the release format | header of `Blackfrost-AI-Inkling-2-Mini-Derisked/model-00001-of-00001.safetensors` (`/tmp/sc117-review/sthdr.py`) | `model.llm.layers.N.attn.wo_ud.weight BF16 [2304, 3072]` |
| Is `wo_ud` raw BF16 in Basalt's pack? | 4 KiB from two of the official SQ-20 `wo_ud` ranges searched in `Inkling-2-Mini-NVFP4-MX8.basalt` (`findpat.py`) | not found. Basalt stores it as Q8 (its converter `tools/convert/checkpoint_inkling2.py:65`: `"attn.wo_ud.weight": ("attn_output.weight", "Q8", "id")`), so the Basalt route goes through the converter (§5.2) |

**Provenance verdict:** clean, as far as files can show. The revision is
pinned, every byte is accounted for, and the edit is confined to exactly
what the README says. What we cannot verify: the edit's quality (it was
measured by SC117 only) and which revision of the base was used. The
untouched 95% is the official `04996a1e` pack, which we already pinned and
ran.

## 3. The claims against our numbers

| Claim (source) | Who measured, how | Credibility, and ours |
|---|---|---|
| SQ-20 `--bench` 2270 prefill / 76.8 decode; live DSpark 75.1 prose, 94.9 code; 13.8K prefill 1,874; 41K prefill 1,411; `--parallel 16` aggregate 392.5 (README, verification.json) | SC117, "RTX 4080 SUPER 32 GiB (sm_89)", Windows WDDM, strata 0.2.19 / 0.1.65, `serve/server.py`, greedy, 448 tokens, 3-5 runs, **thinking off** | Plausible and modest. **The card is odd:** a stock 4080 SUPER has 16 GB, so this is a modded card or a typo; unverifiable either way. Scaled by bandwidth (736 → 1,792 GB/s, ×2.4), 75 / 95 becomes ~183 / ~231. Ours on the 5090: **SQ-20 DSpark 201 / 131** (code / prose, pin, thinking off), **Basalt stock 376 / 307, Derisked 403 / 356** (Strata report §11.2). Speed is unaffected by the edit (their paired 80.5 → 80.1). Nothing here claims more than Basalt does |
| "Lossless" SQ-20 | vendor wording | Means the release's NVFP4/MXFP8 bits repacked, the same as Basalt's "the release's bits" (its README). Not lossless against BF16; the release has no BF16 experts |
| Abliteration cost: five-domain KLD 0.013623, same-top 95.63%, ΔPPL +0.70% (max +0.99%) | SC117, GGUF Q8_0 abliterated vs Q8_0 base, "five-domain" corpus unpublished | Plausible for a rank-1, attention-only edit, and smaller than Blackfrost's DWM edit. **Not comparable** with our 0.0263 (Derisked) vs 0.0128 (stock): ours is pack vs UD-Q8_K_XL on `longtext.txt` and includes quantization. To compare, measure it ourselves the same way (§5.2 step 6) |
| Refusals 98/100 → 9/100 (effort high) / 13/100 (none); ZH 50/50 → 4/50; Strata live probe 10 prompts: C=3, H=6, R=0, D=0, E=1 | SC117's own held-out sets, judge not stated, Wilson intervals given | Self-reported, prompts unpublished; the 10-prompt probe is tiny. Reads as "mostly complies, often hedges (H=6/10)". Policy: uncensored preferred, so this is a plus if it holds. Not a coding claim |
| "Matching unpatched official SQ-20 within 0.5%" (speed) | SC117 | Consistent with §2.3: same bytes except 20 BF16 matrices |
| v0.2.21 release notes: RTX 5090 prefill 9,019 tok/s (SQ-20), 64 requests 1,040-1,134 tok/s total | Niko1221 | Their own figures. Basalt prefill measured 18,098-19,338. No 5090 single-stream decode figure is claimed; the 4090 "92 instead of 20 tok/s after 32K" is a long-context fix, not peak speed |

**No benchmark in the pack is a quality or agentic benchmark.** Nothing
supports "a better model than Derisked" except the edit being smaller.
INK-T2 (Derisked) 49/71 against INK-T2-STOCK 53/71 says a heavier edit cost
about 4 units, within roughly 2x seed noise. A lighter edit might cost less.
Only an arm can tell.

## 4. Engine compatibility and security

### 4.1 Which engine can load it

- **Strata:** format "Strata v1.1"; the README requires "Strata ≥ 0.2.19
  (build 0.1.65+)" with the `strata-inkling` component. Our pin `6a6db7ff`
  (v0.2.20+16, `strata-inkling` built from it) already loaded the official
  v1.1 SQ-20 at `04996a1e`. The abliterated file has the same layout with a
  recomputed digest, so the pin should load it. The `--verify-hash` path
  will check the new digest. For thinking, use v0.2.21 (§1).
- **#6762 (DSpark on CUDA):** still open (`gh api …/issues/6762`), but it
  only affects a **GGUF** drafter. The native SQ packs embed the drafter,
  and the pin's unpatched build ran SQ-20 with DSpark (201 / 131). It does
  not block this pack.
- **Basalt:** reads only `.basalt` packs; it cannot read `.strata`.
  Conversion route: the official HF release format plus the 20 transplanted
  tensors, run through Basalt's converter (§5.2).
- **llama.cpp:** cannot read `.strata`. SC117's companion FIT-GGUF (BF16,
  Q6_K, IQ tiers) is the same edit for llama.cpp. llama.cpp measured
  96.9 tok/s single-stream (Strata report §5), so it is not a speed
  candidate.
- **Maya:** reads only `inkling2`-keyed GGUFs with NVFP4 / Q8_0 experts
  (Maya report). Not this.
- **No Basalt pack of SC117's edit exists** (Hub search "Inkling-2-Mini-Basalt":
  `jesdga`, `nokeepai/…-Unbound-Basalt`, `nathaninline`, `harshav`; none is
  SC117's). Many other abliterations exist (orcarouter, mrfakename,
  IsValorum, Kujira, …), outside this review.

### 4.2 Security

- **The pack: data only.** No Python, no executable, no pickle. The 4 JSON
  configs are templates with `<MODEL_DIR>` / `<STRATA_DIR>` placeholders and
  `"exe": "component:strata-inkling"`. `"lib_dirs": []` is empty. If it
  were set, it would add library search paths, so **we do not use SC117's
  configs**: our serve writes its own config, as `strata-serve.sh` did.
  `chat_template.jinja` is byte-identical to the official pack's template
  (§2.3); its only special call is `raise_exception` on a bad effort, and it
  has no includes and no URLs. URLs in the READMEs: HF repo links,
  `github.com/Niko1221/Strata`, a Ko-fi link and one support image on HF.
  None of these is used by any engine.
- **Strata v0.2.21: a new review is required before anything runs.** Our
  §2 review covered `6a6db7ff`. v0.2.21 is 276 commits later, with ≥300
  files changed (`gh api …/compare/6a6db7ff…v0.2.21`, files capped at 300),
  still one author (Niko1221 uploads every release asset), and its notes add
  "a short test at startup" and a "signed update feed". The rules stand:
  source build at a pinned commit (tag `v0.2.21` → `d46f5f268f8ed797e83a5c63a69bffbd7a581cf6`),
  offline, in the locked-down container, no release binaries, no
  installer, no `strata update`, and `STRATA_UPDATE_CHECK=0` plus no
  network at serve. Focus the delta review on `terminal/serve/` (where the
  thinking change landed), anything that opens a socket or a subprocess,
  the update feed, and `setup.sh`.

## 5. Phase 2 plan (after Max's Basalt switch; needs Max's go for GPU time)

Both paths need the 5090 to themselves. The default (Basalt via
`scripts/engines/basalt.sh`) is stopped before and restored after, in the
same way the ablation queue's `restore()` did. Nothing touches
`~/Documents/openbeast` except `./stop.sh` and `./start.sh -d`. Work dirs:
`~/openbeast-runs/sc117-2026-11-25/{scripts,results,logs,scratch,weights}`.

### 5.1 Path A: Strata v0.2.21 (bounded speed and thinking check, no v5 arm)

| Step | What | Time |
|---|---|---|
| A0 | Delta review `6a6db7ff..d46f5f26` (subagent, read-only), focused as in §4.2; verdict before any build | 1-2 h |
| A1 | Fetch the tag into a bare repo (`git fetch --depth=1 origin d46f5f268f8ed797e83a5c63a69bffbd7a581cf6`), check the commit id, bundle it; build offline, reusing `strata-bench-2026-11-23/scripts/build-strata.sh` with the new tree (`docker run --network none … ob-strata-env:2026-11-23`); record the binary's sha256 | 30 min |
| A2 | Serve: copy `inkling-perf-2026-11-23/scripts/strata-serve.sh` to `sc117-2026-11-25/scripts/strata-serve.sh`, change the binary and tree mounts to the v0.2.21 build, mount `/mnt/9100s4tb/weights/SC117-Inkling-2-Mini-abliterated-Strata:/packs:ro`, pack `Inkling-2-Mini-SQ-20-abliterated.strata`, no `--vision`, `--kv fp8`, `--draft dspark`, `--parallel 1`, CTX 262144, `--dns 127.0.0.1`, `--tmpfs /tmp:…,noexec` (try it; fall back to `exec` only if the engine needs it, and log that), `-e STRATA_UPDATE_CHECK=0 -e STRATA_INKLING_EFFORT=high -e STRATA_INKLING_THINKING_BUDGET=6144`. Keep the rest as before: `--read-only`, `--cap-drop ALL`, `no-new-privileges`, uid 1000, no-egress bridge, `127.0.0.1:8097` only, API key | 10 min |
| A3 | **Gate (Max's rule):** `python3 inkling-perf-2026-11-23/scripts/probe_think_tools.py` against `:8097` (enable_thinking true / false / unset, streamed and not, as in Strata report §11.2), plus `scripts/backends/conformance.sh` at c=1 and c=4. **Stop path A** if `reasoning_content` is empty with thinking on, or a tool call breaks | 15 min |
| A4 | Speed, the same probes as every Inkling row: `python3 scripts/bench.py decode --base http://127.0.0.1:8097 --key "$(cat scratch/strata.key)" --prompts data/prompts.json --warmup --runs 5 --streams {1,4,8} --extra '{"reasoning_effort":"none"}'` and again with `'{"reasoning_effort":"high"}'`; `bench.py prefill --sizes 4096 16384 32768`; `bench.py needle` | 30 min |
| A5 | Context: one 200K-token prompt (INK-T2-STOCK's largest was 202,663) at CTX 262144. If it wedges (#7034), Strata cannot run a T2-comparable arm | 10 min |
| A6 | Basalt control on the same prompts, the same day: `inkling-v5.sh start stock --patch pr632`, then the same `bench.py` lines against `:8099` | 20 min |

Total ~3-4 h, about 1.5 h of it on the GPU. **A Strata v5 arm only if** A3
passes, A5 passes, and A4 beats A6 single-stream. §3 makes that unlikely.

### 5.2 Path B (recommended): the SC117 edit on Basalt

CPU steps B1-B5 can run while the default serves (they only read weights
and write to `sc117-2026-11-25/`). Run them `nice`d.

| Step | What | Time |
|---|---|---|
| B1 | Fetch `thinkingmachines/Inkling-2-Mini` at a pinned revision (Hub `sha` today `c9d02830…`, 20.19 GB: `model-00001-of-00001.safetensors` 19.53 GB, `mtp.safetensors`, tokenizer, `tiktoken/`) to `/mnt/9100s4tb/weights/thinkingmachines-Inkling-2-Mini/`; check each LFS sha256 against the API. Use `hf download … --revision <sha>` on the host, the one networked step; offline path: from beast-ark | 10-20 min |
| B2 | **Reproducibility gate:** convert the untouched release with the measured converter, in the sandbox, exactly as `inkling-perf-2026-11-23/scripts/convert-blackfrost.sh` (`--network none --read-only`, uid 1000, `ob-strata-env:2026-11-23`, `tools/convert/convert.py --checkpoint /ck --out …`). Expect sha256 **`28ec7072…`** = the stock pack. If it differs, the release revision is not the one jesdga converted: then diff (step B5 tool) to see where, and pin the right revision before going on | 5 min |
| B3 | Extract: for each of the 24 `model.llm.layers.N.attn.wo_ud.weight` tensors (offsets from the safetensors header), find their 14,155,776 bytes in the **official** SQ-20 (`…/strata-Inkling-2-Mini-Strata-04996a1e/Inkling-2-Mini-SQ-20.strata`, `wo_ud` stored "24x BF16"); require an exact full-length match for all 24 and offsets inside §2.3's ranges; read the **abliterated** SQ-20 at the same offsets; require layers 0-3 equal and 4-23 different. A short script of ours (`python3 -I`, no pack code); its output is a 20-tensor BF16 blob plus a manifest of offsets and sha256 values | 10 min |
| B4 | Patch: copy the release checkpoint dir to `sc117-2026-11-25/weights/ck-sc117/` (hard copy of the safetensors, other files copied) and overwrite the 20 tensor data ranges in place. Header, dtypes and shapes are unchanged. Record the sha256 of the patched file | 5 min |
| B5 | Convert the patched checkpoint as in B2 → `Inkling-2-Mini-SC117-abliterated-NVFP4-MX8.basalt`; `blockdiff.py` against the stock pack: expect differences only in the 20 `attn_output.weight` (Q8) tensors, plus any header digest | 5 min |
| B6 | GPU, quality: `basalt-bench --quality` (or the §11.2 llama-perplexity KLD on `data/longtext.txt`, 60 x 512) for stock, Derisked and SC117 against the same reference, so the edit's KLD is on our scale; the 16K needle; a 20-prompt refusal spot check (record only) | 30 min |
| B7 | GPU, speed and harness: serve via `inkling-v5.sh` with a new pack case `sc117` (an edit to the rig-local `basalt-serve.sh`/`inkling-v5.sh`: mount `sc117-2026-11-25/weights`, `MODEL=/models-sc117/…`); `bench.py decode` at streams 1 / 4 / 8, thinking off and high, against the same-day stock control; `probe_think_tools.py`; conformance at c=1 and c=4 | 40 min |
| B8 | **v5 arm `I_T2_sc117`**: `{"id": "I_T2_sc117", "base": "I_T2_stock", "model": "Inkling 2 Mini SC117 abliterated (Basalt NVFP4-MX8)", "served_model": "inkling-2-mini", "jobs": 1, "slots": 1}` in `evals/campaigns/v5-thinking-first.json` (PR first). It is INK-T2-STOCK exactly: harness9 tree `f38a29c7` via `--arm-tree`, era `036638dd`, `--patch none` (as INK-T2-STOCK ran), `--answer-room auto`, `--parallel 1 --max-context 262144 --kv fp8`, 8,192 cap, thinking at the template default. The launcher checks that `docker inspect basalt-serve` names the SC117 pack. Launch it as `ink-I_T2-run.sh` launched I_T2 | **~4.5 h** (INK-T2-STOCK: 4 h 18 min) |
| B9 | Register: a `scripts/weights.registry` row (sha256, bytes, local name, source `SC117/Inkling-2-Mini-abliterated-Strata@ae324ac3` + `thinkingmachines/Inkling-2-Mini@<rev>`, "derived"), the recipe in MODELS.md, board row and report | 30 min |

Path B total: ~1 h CPU, ~1.2 h GPU for checks, then ~4.5 h for the arm.
**Why not `--patch pr632`?** INK-T2-STOCK ran without it. To compare
the packs, only the weights may differ. A pr632 follow-up can mirror I_T3.

### 5.3 Reading the result

- The noise margin is INK-T2 vs INK-T2-S1, 2 flips, so ablation.py's MDE is
  about 7 units at that churn. A single `I_T2_sc117` row within ±3 of 53/71
  is a tie. A tie at the same speed, under the uncensored-preferred policy,
  still favours the abliterated pack for the default; that is Max's call.
- The comparison that answers Max's question ("better than
  blackfrost-derisked?") is `I_T2_sc117` vs `I_T2` (Derisked, 49/71,
  same harness and serve).

### 5.4 The overflow harness gap (Max's 2026-11-25 rule)

Strata's overflow errors, at v0.2.21 `terminal/serve/server.py:5636-5659`:
`request exceeds the context window: prompt (N tokens) leaves no room to answer in the context (C); requests are never truncated`,
`… the prompt (N tokens) leaves R of the C-token context, fewer than the F an answer needs; …`,
`… prompt (N tokens) + max tokens (M) exceeds the context (C); …`. The Anthropic path
(`server_parts/base.py:238`) gives `input length and \`max_tokens\` exceed context limit: …`.
**None of the four match `agents/runner.py` `_CTX_OVERFLOW_RE`** (lines
447-454; tested with the regex copied verbatim: 4/4 False). The second
does contain `-token context`, but the regex needs `-token context window`.
`_CTX_MSG_RE` does not match either. Strata also clamps `max_tokens` to the
room left by default (`fit_max_tokens`, server.py:4180), so it often answers
short instead of erroring. Under the rule, a Strata arm's overflows would show
as `server_error` and must be put in `evals/boards/v5/overflow.json` by hand
(DECISIONS-2026-11-25 §3). Fixing the regex is the next-era proposal in
HARNESS_NOTES (Basalt's wording has the same gap). **Path B does not have this
problem beyond what INK-T2-STOCK already had.**

## 6. Blockers, honestly

1. **Strata thinking is unmeasured.** In source since `39b8407c` and in
   v0.2.21, but #7084 (uncapped second thinking block, empty replies) is
   open, and the serve default is `low`, not the template's 0.9. If A3 fails,
   the Strata path ends by Max's rule.
2. **Strata is about half Basalt's single-stream speed** on this exact pack
   family (201 / 131 vs 376 / 307), and v0.2.21 claims nothing that closes
   that gap on the 5090.
3. **262,144 context on Strata is unproven** (#7034, >65K wedge on a 3090).
   T2 reached 202,663-token prompts, so a smaller window would turn sessions
   into overflow losses.
4. **The v0.2.21 delta is unreviewed** (276 commits).
5. **Path B depends on B2** (the converter reproducing the stock pack from
   a pinned release revision). If it does not, we still get a valid
   SC117-on-Basalt pack, but it is no longer a one-variable comparison
   with INK-T2-STOCK, and the report must say so.
6. **The quality claims are SC117's own.** KLD and refusal rates were
   measured on GGUF Q8_0 with unpublished prompt sets.

## 7. Files and commands

| Where | What |
|---|---|
| rig `/mnt/9100s4tb/weights/SC117-Inkling-2-Mini-abliterated-Strata/` | the pack (untouched) |
| rig `/tmp/sc117-review/sha_check.txt`, `sha_all.txt` | `sha256sum -c` (5/5 OK) and every pack hash |
| rig `/tmp/sc117-review/blockdiff-sq20.txt`, `blockdiff.py` | official vs abliterated SQ-20, 64 KiB block diff |
| rig `/tmp/sc117-review/sthdr.py`, `findpat.py` | safetensors header reader; byte-pattern search (ours) |
| rig `~/openbeast-runs/inkling-perf-2026-11-23/weights/strata-Inkling-2-Mini-Strata-04996a1e/` | the official SQ packs (control) |
| rig `~/openbeast-runs/inkling-perf-2026-11-23/scripts/{strata-serve.sh,bench.py,probe_think_tools.py,convert-blackfrost.sh,inkling-v5.sh,basalt-serve.sh}` | the scripts Phase 2 reuses |
| Hub | `curl -s 'https://huggingface.co/api/models/SC117/Inkling-2-Mini-abliterated-Strata?blobs=true'`, `…/commits/main`, `…/api/users/SC117/overview`, `…/api/models/strata-engine/Inkling-2-Mini-Strata?blobs=true`, `…/api/models/thinkingmachines/Inkling-2-Mini?blobs=true` |
| GitHub | `gh api repos/Niko1221/Strata/{commits/main,releases,releases/tags/v0.2.21,compare/…,issues/7084,issues/6762}`; `gh api 'repos/Niko1221/Strata/contents/terminal/serve/inkling_dialect.py?ref=main'`; `…/contents/terminal/strata_control/inkling_family.py?ref=main`; `…/contents/terminal/serve/server.py?ref=v0.2.21` |
