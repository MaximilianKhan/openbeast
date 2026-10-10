# 6. Serving: the adapter's decode cost and how much of it kernels remove

## 6.1 The cost on stock llama.cpp

Every corrected configuration in §4 and §5 carries a decode cost that a bare quantized file does not. On stock llama.cpp the adapter path adds two unfused matrix-vector launches per projection. At batch 1 this costs 33-39% of decode speed at 0.6B (667 → 444 and 407 tok/s). At 27B it costs 33% with F16 rank-64 factors (99.7 → 66.5 tok/s) and 19% with Q8_0 factors (99.7 → 81.0) [source: research/lowrank/RESULTS_ROLLUP.md; experiments/35-final-reanalysis/RESULTS.md]. Others report the same pattern: adapter operations at batch 1 are bound by launches and not by rank (arXiv:2310.18547; arXiv:2603.11873; arXiv:2605.08314; CALDERA's Table 6, arXiv:2405.18886). A corrected configuration that is competitive with a rung on bytes is therefore not competitive with it on stock llama.cpp today.

The kernels below remove most of that cost on local, unmerged patches. They leave perplexity digit-identical and each has a switch to disable it. All speed comparisons are same-session A/B pairs, because drift between sessions on identical configurations reached 9% and invalidated one number in our first tables (86.3 tok/s, 82.3 on re-measurement) [source: research/lowrank/review/adversarial-stats.md §F2-F3].

**Evidence grade.** Only one speed result in this section has raw benchmark output in the repository: the N = 10 interleaved pair at 27B (§6.2). Every other speed number is transcribed from the per-phase reports, which record the measurements as prose tables. All of them come from one GPU and one host, at a pinned build (llama.cpp 0ef6e55ed). The patch set has since been rebased onto b10865 and no speed number was re-measured after the rebase.

## 6.2 Three phases of kernel fusion

**Window fusion.** The five-node subgraph llama.cpp emits per adapted projection (W·x, A·x, B·t, scale, add) is matched in the CUDA backend and collapsed to two kernels. The result was +10% at rank 16 and no change at rank 64: removing 588 of 784 extra kernel launches per token did not move rank-64 decode, so launch count was not the bottleneck on an idle GPU [source: research/lowrank/experiments/14-fused-kernel/REPORT.md].

**Folding A into the activation-quantize kernel.** Following the design of SVDQuant (arXiv:2411.05007), we fold t = A·x into the existing activation-quantize launch and B·t into the matrix-vector epilogue. Rank-64 decode at 0.6B went from 476 to 685 tok/s (+44%), which reduced the adapter cost in that session from 45% to 20%. The constant cost had been serialization: the epilogue loaded B and t after the main dot-product loop, adding a memory-latency round to every kernel. Loading static operands before the grid-dependency synchronization point removes it [source: research/lowrank/experiments/14-fused-kernel/REPORT-PHASE2.md].

**Quantized factors and register pressure.** Accepting Q8_0 factors in the fused paths gave +62% at 0.6B (394 → 638) and at first slowed 27B by 6.6%, because unfused Q8_0 factors already use an int8 kernel upstream. Two size gates restored +0.4% at 27B [source: research/lowrank/experiments/14-fused-kernel/REPORT-PHASE2B.md]. The remaining 27B cost was register pressure: the fused kernel carried about 19 extra registers (61 → 80), which lowered occupancy. Moving the adapter dot product ahead of the main loop, with its result held in shared memory, restored 62 registers. Same-session pairs after that change: 0.6B 391 → 702 tok/s (+79%, one pair); 27B 84.7 → 86.9 (+2.6%, one pair) [source: research/lowrank/experiments/14-fused-kernel/REPORT-PHASE3.md].

Re-measured on a quiet host with interleaved blocks (N = 10, median and interquartile range), the 27B corrected configuration decodes at 88.1 [88.0-88.3] tok/s fused against 85.8 [85.7-85.8] unfused: +2.7%, against a measured ceiling of +4.9% for any epilogue design [source: research/lowrank/experiments/27-bf16-rederivation/r8-flagship-bench.txt].

Two practices came out of this that may transfer to other memory-bound matrix-vector kernels: load static operands before the grid-dependency synchronization, and keep the epilogue from adding registers to the main loop by holding its state in shared memory.

## 6.3 An allocator fix for stream concurrency

llama.cpp's CUDA graph optimization was rejecting stream concurrency on adapter graphs, correctly: the allocator keeps one global free list, so a buffer freed in one branch of a fork can be handed to a tensor in a concurrent branch. Our change pins buffers within a fork-join region: frees inside a region are deferred to the join, and an in-place guard keeps the last consumer of a forked tensor from taking a buffer another stream still reads. The backend test suite (12,996 cases) passes, perplexity is digit-identical, and 200-token greedy generation is string-identical with the change on and off on three configurations.

The speed result is modest and was measured on a loaded host (load 40-66 on 32 threads throughout). At 0.6B, base-model decode rose in two sessions by 11% (763 → 849 tok/s) and by 19.6% (794 → 950); the report calls the second the best pair. With an adapter the gain was 7-12%. At 27B, six alternated sessions showed no difference outside noise: streams cover only the one-in-four full-attention layers of the hybrid architecture, and the large matrix-vector products already fill the GPU [source: research/lowrank/experiments/25-alloc-concurrency/REPORT.md]. The change does not depend on adapters.

## 6.4 Where the decode cost stands

At 0.6B the adapter cost on the patched build is about 20%, down from 45%. At 27B the fused corrected configuration (88.1 tok/s) is within the +4.9% ceiling of the unfused one, so with Q8_0 factors little of the remaining cost is in the epilogue. What remains against a bare rung is the adapter's VRAM and the per-token chain of extra nodes, which published work prices at 1.3-2 µs per node (arXiv:2512.22219). None of the patches is merged upstream.
