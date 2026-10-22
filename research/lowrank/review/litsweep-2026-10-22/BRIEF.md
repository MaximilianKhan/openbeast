# Literature sweep brief — beast-rank paper (TMLR submission, ~2026-10-24)

Paper full text (read the parts you need): /home/max/Documents/openbeast/research/lowrank/paper/final/paper.md
Related work section: /home/max/Documents/openbeast/research/lowrank/paper/final/sections/07-related-work.md
Already-cited arXiv IDs (one per line): cited.txt (this directory)
Today is 2026-10-22. Your knowledge cutoff is older; papers from 2025-2026 matter most.

## The paper's claims (what must "hold up")
C1 Re-rounding: re-choosing a llama.cpp Q2_K file's codes (grid/scales frozen, GPTQ sweep under captured activation covariance) cuts KLD 33% at Qwen3.5-0.8B, but at Llama-3.1-8B and Qwen3.6-27B gives no resolved gain and DAMAGES held-out corpora (FineWeb-Edu, code). Re-rounding damages text kinds its calibration doesn't cover, at every size. Letting GPTQ refit the grid beats frozen grid in 11/12 cells.
C2 Whitened low-rank error correction (QERA/LQER/EoRA lineage) on llama.cpp K/I-quants improves its base on every corpus, less off-distribution (0.25-0.96 of wikitext gain), but LOSES to stock types: a lower-byte I-quant beats it at 8B/27B; a SAME-BYTE mixed-stock-type file (no adapter) beats it on every corpus at 27B and 3x at 0.8B. Recovered fraction grows as base gets thinner (7/12/32% at Q2_K/IQ2_XS/IQ1_S). Three placements of low-rank bytes (post-hoc / pre-carve / shared basis) indistinguishable. Rank capture sublinear, r/d worst fit. Low-rank doesn't compress weights (95% energy needs 50-80% rank; residual 90% energy needs ~62% rank).
C3 KLD vs quality: released GSQ-RCO trained checkpoints (Qwen3.8-27B, arXiv:2604.18556 / RCO arXiv:2605.00649) are FURTHER from BF16 by KLD than the untrained Unsloth UD file at matched bytes, yet assign wikitext higher likelihood (lower NLL), resolved at IQ3. So KLD-to-reference measures fidelity, not quality, when comparing trained vs untrained quantized artifacts. A single agentic-coding-suite run showed GSQ +12 tasks (88 vs 76); a 3-run replication (F) is pending.
C4 Fused LoRA/adapter decode kernels in llama.cpp: +79% decode at 0.6B, +2.7% at 27B; stock adapter path costs 20-40% decode.
C5 Methodology: held-out corpora, single-step provenance, same-byte controls, paired per-chunk stats, pre-registration; surrogates (layer reconstruction) failed to predict outcome 7 times.

## Your job
Search arXiv WIDELY for your assigned slice. Use the arXiv API directly with curl for breadth, e.g.
  curl -s "https://export.arxiv.org/api/query?search_query=abs:%22error%20compensation%22+AND+abs:quantization&start=0&max_results=200&sortBy=submittedDate&sortOrder=descending"
(fields: ti, abs, all; AND/OR/ANDNOT; quote phrases with %22; be polite: ~3 s between calls). Parse titles/ids/dates/abstracts with python3 -I (stdlib xml). Run many query variants; aim to screen several hundred abstracts for your slice. Also use WebSearch/WebFetch for non-arXiv venues (OpenReview, ACL anthology, NeurIPS/ICLR/ICML 2025-2026, GitHub llama.cpp discussions) where relevant. Read full text (arxiv.org/abs or /html/<id>) of the closest 5-15 papers before judging them.

Do NOT modify any file in the repo. Write your report to the path given in your task, then reply with a ≤250-word summary.

## Report format (markdown)
1. Coverage: queries run, number of abstracts screened, date range.
2. THREATS (most important): papers that contradict, pre-empt (same finding already published), or weaken a claim C1-C5. For each: arXiv id, title, date, which claim, exact relevant finding (quote numbers), severity (fatal / must-cite-and-discuss / should-cite / minor), and the sentence/section of our paper it affects.
3. SUPPORT: papers that corroborate a claim (could be cited to strengthen it).
4. MUST-CITE MISSING: highly relevant papers not in cited.txt.
5. NEXT-STEP IDEAS: experiments these papers suggest for us after this paper, ranked by expected payoff on a single RTX 5090 with llama.cpp.
6. Verify every id/title/claim you report by actually fetching it — no ids from memory. Mark anything unverified.
