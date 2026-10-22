# R6: serving low-rank branches on quantized bases (C4), plus a reference venue check

Sweep date: 2026-10-22. Scratch data is in `sweep/r6/`: `hf6.json` (HF papers index), `sweep.jsonl` (arXiv API), `orv2.out` (OpenReview venue lookups) and `pdf/dlora.txt`.

## 1. Coverage

- **arXiv API.** Only 1 of 8 planned queries completed: `abs:LoRA AND abs:serving`, 200 hits. Other agents running sweeps from this IP kept the API, arxiv.org/search, OpenAlex and Semantic Scholar on HTTP 429 for most of the session, so the remaining 7 queries failed after up to 14 backoff retries. DBLP served a bot challenge.
- **Hugging Face papers search API (arXiv-backed).** 30 query variants returned 1,613 unique papers. Sample queries: multi-LoRA serving, fused LoRA kernel, S-LoRA, dynamic adapter latency, low-rank compensation kernel, low-rank branch fused kernel, SVDQuant, error-compensation serving, unmerged LoRA inference, llama.cpp, GGUF quantization, on-device LoRA, adapter decode overhead, GEMV kernels, megakernel, CUDA-graph launch overhead, MoE compensators, W4A4 low-rank branch.
- **Screening.** 1,739 unique abstracts in total, dated 2016-02 to 2026-10-20. A keyword filter for low-rank/adapter plus latency/kernel/serving terms left about 300 for title-and-abstract screening.
- **Web search.** About 25 WebSearch queries covered non-arXiv venues (OSDI, MLSys, MobiSys, AAAI, PMLR, USENIX), the ONNX Runtime repo and the llama.cpp GitHub tracker. For the tracker I used `gh` search over all issues and PRs mentioning lora, fuse, speed, decode or tok/s.
- **Full text read** for the closest papers: SPEAR, TwinQuant, LoPRo, FLRQ, GlowQ, Punica §7.1, dLoRA §3 (PDF), Signs-Beat-Floats Table 2, EdgeLoRA, DecDEC (abstract), LoRA-Switch (abstract), ONNX Runtime PR #33370, llama.cpp #27093, #30964 and #11410.

## 2. Threats

None is fatal. Nothing I found measures adapter or correction decode cost on llama.cpp GGUF K/I-quants, or ships a fused decode path there. Upstream llama.cpp has no merged fused-LoRA PR: I searched merged PRs with "lora" in the title, and the only generic fusion found is Vulkan's mul_mat+add (#16868, #17287). The threats below are to one sentence and to the framing of novelty.

### T1. SPEAR, arXiv:2606.11244 (2026-06-04, cs.AR, no venue listed). Must cite and discuss.
"SPEAR: A System for Post-Quantization Error-Adaptive Recovery Enabling Efficient Low-Bit LLM Serving", Liu, Li, Que, Yang, Shao, Hu.

- **Method.** Adds gated low-rank error compensators (`y = Ŵx + α·B(γ(Ax) ⊙ Ax)`, INT8 factors) to W4 MARLIN.
- **Decode fusion.** At batch 1 it fuses the compensator into the GEMM epilogue, reusing registers and shared memory freed after the main loop. Prefill uses a semi-fused, overlapped path.
- **Measured cost on GH200, M=1:**
  - A naive unfused compensator raises decode latency about 5x over plain W4 at 1B, 3B and 7B.
  - Fused, it reaches 2.5 ms/token at 1B, "only +25% over W4 MARLIN".
  - At 70B it is +6 to +14% over W4.
  - Table 5 (7B, TP=2): naive 29.01 ms, fused without P2P 11.45 ms, full SPEAR 4.73 ms, against a W4 reference of 6.71 ms.
- **Claim affected.** C4 and §8 Serving: "Few released correction methods ship a fused decode path…". This is a 2026 system that fuses a quantization-error low-rank corrector into the decode epilogue, the same design idea as our §6.2 phase 2/3. We arrived at it independently, but it now has to be positioned against SPEAR.
- **Overlap.** Its "+25% at 1B and +6-14% at 70B" profile matches our "large gain at 0.6B, small at 27B".
- **Difference.** It targets MARLIN W4 on GH200 under vLLM. It does not mention llama.cpp, GGUF, SVDQuant or Punica.

### T2. TwinQuant, arXiv:2606.01556 (ICML 2026; already cited for rank). Must discuss in Serving.
- Ships a "fused dual-component kernel that pipelines the two-stage low-rank computation on-chip and merges both components with a single epilogue", at rank 128 with W4A4/W4A8 and 4-bit factors.
- At batch 1 on an RTX 4090 (LLaMA3-8B q_proj), fusion takes the kernel from 0.226 ms to 0.113 ms (2.00x). On an L20 it is 0.229 to 0.120 ms.
- Low-rank latency overhead at rank 128 is 3.8-8.2% across six models (LLaMA3-8B: 4.3%).
- **Claim affected.** §8 Serving names SVDQuant as the fused design "for diffusion models". TwinQuant is the LLM counterpart and we cite it only for spectra. It weakens "few … ship a fused decode path". Code release is not stated.

### T3. FLRQ, arXiv:2601.05684 (now AAAI 2026; already cited). Should cite in Serving.
- "We implemented an efficient fusion kernel for low-rank quantization to the AutoGPTQ quantization framework", W4A16 on A100: "FLRQ introduces only a 4% to 6% in inference latency".
- Its Table 5 gives 4.8% at average rank 40 against 26.7% for Quip#+CALDERA at rank 256.
- Another released correction method with a fused decode path. We already cite FLRQ, so §8 Serving should add it to the list.

### T4. DecDEC, arXiv:2412.20185 (OSDI 2025). Should cite.
- Dynamic residual (not low-rank) error compensation for 3-bit weights, run as a fused GPU kernel.
- "1.7% inference slowdown on an NVIDIA RTX 4050 Mobile"; Llama-3-8B-Instruct 3-bit perplexity goes from 10.15 to 9.12, beating the 3.5-bit model.
- The closest consumer-GPU, batch-1 precedent for "a correction must be nearly free at decode". At 1.7% it is a stronger number than ours, so it sets the bar a reviewer will compare §6 against. It also contrasts with C2: it beats a bigger bit-width, whereas our correction loses to stock types.

### T5. LoPRo, arXiv:2601.19675 (2026-01-27, no venue). Minor; useful comparison point.
- Rank-16 low-rank component on GPTQModel's W4A16 kernel, decode at batch 1.
- 7B: 93.3 to 83.7 tok/s, of which "low-rank overhead" is 6.0%. 13B: 6.2%.
- It shows that published low-rank decode costs at 7-13B are about 5-8% (also TwinQuant and FLRQ). Our 27B fused residual cost is 88.1 against a 99.7 bare rung, about 12%, with rank 64 and Q8_0 factors. A reviewer may ask why ours is about twice theirs. Plausible answers are the 2-bit base (fewer bytes to hide behind) and per-node graph overhead. §6.4 should say this explicitly.

### T6. GlowQ (arXiv:2603.25385, now ICLR 2026) and TileQ (arXiv:2605.09281, now ICML 2026). Minor.
- GlowQ reports batch-1 RTX 4090 decode with custom W4A16 kernels, but only against a "Layerwise" low-rank baseline, never against a plain quantized model. Throughput is +9.6% (GlowQ) or +37% (GlowQ-S, selective restore) over Layerwise.
- TileQ "merges several low-rank expert computations into one pass".
- Neither threatens a claim. Both are now venue papers that §8 cites as preprints (see §7 below).

### Not threats: upstream and industry evidence that agrees with C4's 20-40% stock cost
- **llama.cpp issue #27093** (2026-08-14, open), "very tiny LoRA causes severe performance degradation". A rank-1 LoRA on DeepSeek-V4-Flash Q8 with CPU-MoE takes token generation from ">10 t/s" to 7.28 t/s, about 27% or more. A comment there reports a fork kernel for mul_mat_id LoRA (PP512 200 to 1000+ t/s on Strix Halo) and says "Optimizing TG is another issue". This is independent upstream evidence of a rank-independent cost.
- **llama.cpp PR #30964** (opened today, 2026-10-22, open). `llama-batched-bench` silently ignored `--lora`, so LoRA benchmarks with that tool measured the base model. After the fix, on Apple M4 Metal with Qwen3.5-0.8B, decode drops from 89.9 to 79.4, 77.9 and 72.4 tok/s at rank 16, 32 and 64 (12-19%).
  - I checked our experiments: E14 and E27 used llama-server, llama-cli and llama-perplexity, never batched-bench, so our numbers are not affected by that bug.
  - Worth one footnote, since anyone reproducing §6 with batched-bench before this fix would have measured zero cost.
- **llama.cpp PR #11410** (2025-01-25, still open), "Llama-bench: allow benchmarking lora impact". llama-bench still cannot measure LoRA cost, which explains why no published llama.cpp adapter-cost numbers exist.
- **ONNX Runtime PR #33370** (draft, opened 2026-10-12/14, not merged), "[WebGPU] Fuse rank-32 quantized LoRA with MatMulNBits".
  - INT4 base, INT8 rank-32 adapter, decode "fuse_down" on an RTX 5080 at batch 1.
  - Old main runs 261.5 tok/s base and 115.7 with an adapter (-56%). The optimized build runs 236.1 off and 205.7 on.
  - Stated target: adapter-on decode overhead ≤10%, "not consistently met".
  - This is a shipped-in-progress fused decode path for an adapter on a quantized base on a consumer GPU, in another runtime. Cite as concurrent engineering.

## 3. Support (citable corroboration)

| Paper | Finding (verified) | Supports |
|---|---|---|
| dLoRA, OSDI 2024 (Wu et al.; no arXiv) | "the execution time for the LoRA adapter computation BAx is 38.9% of the baseline LLM computation Wx" (§3, Fig. 5) | §6: "costs 33-39% … at 0.6B". A near-exact match from a peer-reviewed systems venue |
| Punica, arXiv:2310.18547 (MLSys 2024) §7.1 | "The latency of a single request batch is around 42µs for all four ranks"; abstract: "only adding 2ms latency per token" | §6: "bound by launches and not by rank" (already cited as [31]) |
| LoRA-Switch, arXiv:2405.17741 (2024, no venue on arXiv) | Dynamic adapters slow "the decoding speed by 2.5+ times"; root cause "fragmented CUDA kernel calls"; a fused kernel cuts decode latency more than 2.4x | Launch-bound cost; same group as AdaFuse [32] |
| Signs Beat Floats, arXiv:2605.24058 (2026-05-22) Table 2 | LLaMA-2-7B on a 4-bit base, H100 NVL, prefill: base 19.3 ms, unmerged FP16 LoRA r=1 32.7, r=2 33.4, r=16 33.5 ms | Rank-independent unmerged cost (+69% at r=1) |
| PreFT, arXiv:2605.14217 (2026-05-14) | Serving adapters "harms throughput, even with specialised kernels"; decode "has far lower throughput when serving multiple adapters" | Decode is where adapters cost |
| SPEAR, arXiv:2606.11244 | Naive compensator about 5x decode; fused +25% at 1B, +6-14% at 70B | Shape of our 0.6B vs 27B result |
| Scale-QLoRA, arXiv:2609.04526 (2026-09-03) | Merging a LoRA into a native 4-bit checkpoint "deletes the adaptation, by up to 39 pp, because against an already-on-grid base the reconstruction optimum is that base" | Why a correction on a quantized base must be served unmerged, which motivates §6 |
| EdgeLoRA, arXiv:2507.01438 (MobiSys 2025) | Built on llama.cpp ("our implementation is based on llama.cpp"); batched multi-adapter serving on Jetson Orin and Raspberry Pi 5; Q8_0/Q4_0 bases, Q8_0 adapters; 2-4x multi-adapter throughput over llama.cpp. No single-adapter batch-1 decode measurement | Closest llama.cpp adapter-serving paper. Does not pre-empt §6 |

## 4. Must-cite missing (not in cited.txt)

1. **SPEAR**, arXiv:2606.11244. Fused decode-epilogue low-rank error compensator. Concurrent design to §6.2. Highest priority.
2. **DecDEC**, arXiv:2412.20185, OSDI 2025. Fused residual compensation at 1.7% slowdown on a consumer GPU.
3. **dLoRA**, OSDI 2024 (USENIX proceedings, no arXiv id). The 38.9% adapter-cost figure.
4. **EdgeLoRA**, arXiv:2507.01438, MobiSys 2025. LoRA serving built on llama.cpp.
5. **Scale-QLoRA**, arXiv:2609.04526. Merging into an on-grid quantized base erases the adapter, which justifies unmerged serving.
6. **llama.cpp #27093, #30964 and #11410** (cite like #8831). Upstream measurements and tooling gaps.
7. Should cite:
   - LoRA-Switch, arXiv:2405.17741.
   - ONNX Runtime PR #33370.
   - LoPRo, arXiv:2601.19675.
   - ASER, arXiv:2411.07762, AAAI 2025 per its arXiv comment. Whitening-SVD low-rank error reconstruction; belongs in the C2 lineage paragraph next to LQER/QERA and is outside my slice, but not cited.
   - SERQ, arXiv:2603.08185, "Accepted at ICLR 2026" per its arXiv comment. Single-matrix low-rank reconstruction.
8. Optional:
   - S-LoRA, arXiv:2311.03285.
   - CaraServe, arXiv:2401.11240.
   - MoLoRA, arXiv:2603.15965 (BGMV wins at small batch).
   - Disaggregated Quantization, arXiv:2609.26333. It uses released Qwen3.8-27B GGUF decoders; relevant to the C3 slice, flagged for whoever covers it.

**Suggested rewrite of the §8 Serving sentence.** "Fused decode paths for correction branches now exist for GPU W4 kernels (SPEAR, TwinQuant, FLRQ; DecDEC for a non-low-rank residual; SVDQuant for diffusion), at 2-25% residual cost. EoRA ships unfused, CALDERA's Table 6 records the loss, and MiLo's compensator is unfused. None targets llama.cpp's K/I-quant formats, where llama.cpp has no fused LoRA path (an open upstream issue reports about 27% decode loss from a rank-1 adapter)."

## 5. Next-step ideas (single RTX 5090 and llama.cpp; ranked by expected payoff)

1. **Correction that merges for free: train or fit only the K-quant block scales (d/dmin), freezing codes.** This is the GGUF analogue of Scale-QLoRA and QA-LoRA. Decode cost is exactly zero, there is no adapter, and the file is still standard GGUF. It complements our frozen-grid re-rounder, which re-chose codes, by re-choosing scales. Cheap: CPU fit against the captured Grams. It removes C4's problem outright and is the most direct fix for "loses to stock types at equal bytes *and* costs decode".
2. **Selective restore.** Apply the correction only on the tensors that carry most of the KLD (GlowQ-S: +37% throughput; SPEAR applies compensators only to sensitive layers). We already know per-tensor KLD contributions, so measure the KLD/decode Pareto curve with corrections on the top-k tensors. Hours of GPU time.
3. **Upstream measurement.** Finish or revive llama-bench LoRA support (#11410), or the `-m model+lora` syntax slaren proposed, and publish a K-quant × rank × model-size decode-cost table. This converts §6 into a reproducible upstream benchmark, and #30964 shows the community currently cannot measure this correctly.
4. **Port SPEAR's prefill/decode dispatch split.** Fused epilogue only for M=1; semi-fused, overlapped path for prefill. Our §6 is decode-only, and prefill cost of the adapter path in llama.cpp is unmeasured in the paper (#27093's PP numbers suggest it is large for MoE mul_mat_id).
5. **Prefill-only correction** (PreFT/LoRA-Drop idea). Apply the correction during prefill only, so the KV cache is built by the better model, and decode on the bare file. A one-flag experiment that costs nothing at decode. If most of the KLD gain survives, it is a new operating point.
6. **CUDA-graph or megakernel residual.** MPK (OSDI 2026) for the remaining per-node chain at 27B (§6.4). Lower payoff: our measured epilogue ceiling is +4.9%.

## 6. Reference venue check (part b)

Method: OpenReview API v2 `notes/search`, whose `venue`/`venueid` fields carry the conference decision; publisher pages (proceedings.mlsys.org, proceedings.mlr.press, usenix.org); arXiv metadata (journal_ref and comments, fetched for all 26 arXiv-only entries); and web search. DBLP was behind a bot challenge, so DBLP itself was not read directly. The OpenReview `dblp.org/conf/...` venueids are DBLP-imported records.

### The three withheld references

1. **LoftQ** (arXiv:2310.08659). **ICLR 2024 (oral).** OpenReview forum LzPWWPAdY4 has venue "ICLR 2024 oral", venueid ICLR.cc/2024/Conference; DBLP record conf/ICLR/2024. Also iclr.cc/virtual/2024/oral/19765.
   > Yixiao Li, Yifan Yu, Chen Liang, Pengcheng He, Nikos Karampatziakis, Weizhu Chen, Tuo Zhao. LoftQ: LoRA-Fine-Tuning-aware Quantization for Large Language Models. In *The Twelfth International Conference on Learning Representations (ICLR)*, 2024. https://openreview.net/forum?id=LzPWWPAdY4
2. **LQ-LoRA** (arXiv:2311.12023). **ICLR 2024 (poster).** OpenReview forum xw29VvOMmU has venue "ICLR 2024 poster", venueid ICLR.cc/2024/Conference; also NSF PAR 10512534.
   > Han Guo, Philip Greengard, Eric P. Xing, Yoon Kim. LQ-LoRA: Low-rank plus Quantized Matrix Decomposition for Efficient Language Model Finetuning. In *The Twelfth International Conference on Learning Representations (ICLR)*, 2024. https://openreview.net/forum?id=xw29VvOMmU
3. **Punica** (arXiv:2310.18547). **MLSys 2024, not SOSP.** proceedings.mlsys.org lists it in "Proceedings of Machine Learning and Systems 6 (MLSys 2024)"; OpenReview DBLP record conf/MLSYS/2024. Page numbers are not shown on the proceedings page.
   > Lequn Chen, Zihao Ye, Yongji Wu, Danyang Zhuo, Luis Ceze, Arvind Krishnamurthy. Punica: Multi-Tenant LoRA Serving. In *Proceedings of Machine Learning and Systems 6 (MLSys 2024)*, 2024. https://proceedings.mlsys.org/paper_files/paper/2024/hash/054de805fcceb78a201f5e9d53c85908-Abstract-Conference.html

### Other refs.bib entries listed as arXiv-only that now have a venue (7)

| Entry | Venue now | Evidence |
|---|---|---|
| MiLo, arXiv:2504.02658 | **MLSys 2025** | OpenReview venueid MLSys.org/2025/Conference (forum NXVXiJmhe1); mlsys.org/virtual/2025/3266 |
| GlowQ, arXiv:2603.25385 | **ICLR 2026 (poster)** | OpenReview "ICLR 2026 Poster", ICLR.cc/2026/Conference (forum kVojSLUcvS); arXiv PDF header "Published as a conference paper at ICLR 2026" |
| Sheared LLaMA, arXiv:2310.06694 | **ICLR 2024 (poster)** | OpenReview "ICLR 2024 poster" (forum 09iOdaeOzp); DBLP conf/ICLR/2024 |
| Compact LMs via Pruning and KD (Minitron), arXiv:2407.14679 | **NeurIPS 2024 (poster)** | OpenReview "NeurIPS 2024 poster", NeurIPS.cc/2024/Conference (forum 9U0nLnNMJ7); DBLP conf/NIPS/2024 |
| MPK, arXiv:2512.22219 | **OSDI 2026**, pp. 1909-1926 | usenix.org/conference/osdi26/presentation/cheng: "20th USENIX Symposium on Operating Systems Design and Implementation (OSDI '26)", BibTeX pages 1909-1926 |
| FLRQ, arXiv:2601.05684 | **AAAI 2026** | DOI 10.1609/aaai.v40i26.39283 (OpenAlex: Proceedings of the AAAI Conference on Artificial Intelligence 40(26)); OpenReview "AAAI 2026" record |
| TileQ, arXiv:2605.09281 | **ICML 2026**, PMLR 306:36981-37010 | proceedings.mlr.press/v306/gu26c.html |

**Author-name issue.** refs.bib has FLRQ's first author as "Hongyaoxing **Gul**", copied from the arXiv listing, which misspells it. The arXiv submission history, AAAI and TileQ/PMLR all spell it "Hongyaoxing **Gu**". Use "Gu".

**Withheld-venue note.** The note on Punica ("may have appeared at SOSP 2024 instead") is wrong: it is MLSys 2024.

### Still arXiv-only or under review (unchanged)

- **No venue found:** ZeroQuant-V2 2303.08302, GSQ 2604.18556, GPTQ-intrinsic LoRA 2606.01412, OBD-LLM 2604.00821, Operator Factorization 2507.09428, Displacement Is Not Direction 2606.19558, AWSRC 2608.23144, ARHQ 2605.00140, 2512.17073, FlashSVD v1.5 2605.08314.
  - FlashSVD v1.5 is different from the v1 FlashSVD paper (arXiv:2508.01506), which is AAAI 2026. Do not conflate them.
- **ICLR 2027 submissions under review:** ReQuant 2608.07019, SchurQuant 2608.15567, RCO 2605.00649.
- **Rejected at ICLR 2025, no later venue found:** Super Weight 2411.07191, LRC 2412.07902.
- **EoRA** 2410.21271: already "ICLR Workshop 2026". It was accepted at two ICLR 2026 workshops (TTU main track and ICBINB), so the entry is fine. Name the workshop if the style wants it.

**Paper text.** The reference list in paper.md also prints [27] GlowQ, [31] Punica and [35] MPK as "arXiv preprint". Those, plus MiLo, FLRQ, TileQ, LoftQ, LQ-LoRA, Sheared LLaMA and Minitron, should change wherever paper.md lists them.

## 7. Verification notes

- **Fetched and verified:** every id, title and number above, via arXiv abs/HTML, OpenReview API, publisher pages or GitHub, with these exceptions:
  - dLoRA has no arXiv id. It was verified from the USENIX OSDI '24 PDF text.
  - The ONNX Runtime PR's model is not stated in the PR.
  - The OpenReview forum pages hit a browser challenge, so venue fields come from the OpenReview search API (`venue`/`venueid`), not the rendered page.
- **Unverified, seen only in search snippets and not used for any claim:** LASER (arXiv:2606.00573), MosaicQuant's 55% sparse-kernel figure (arXiv:2606.15652), and "InvisibleLoRA" (an ICLR 2027 submission titled "Fast Multi-LoRA Serving Without Per-Token LoRA Decoding", OpenReview forum eLUJLZDg84, unreadable).
