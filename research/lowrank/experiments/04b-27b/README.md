> ⚠ see review/ corrections 2026-08-04

# E04c — scale replication on the production 27B

The 0.6B triangle (../04-served-v0/) ended with a named null: correction
beats IQ points but loses to Q3_K_M at equal bytes. This experiment tests
the **scale hypothesis**: rank bytes cost r(m+n) against the m·n they
correct, so 27B-class matrices buy the same rank ~5× cheaper in bpw —
and big models tolerate 2-bit far better to begin with.

**Pivot note:** the original 9B step (GLM-4-9B) died on conversion-era rot
("cannot find tokenizer merges" — its GGUF predates the requirement).
Pivoted UP to the real target: **Qwen3.6-27B heretic-v2 Q6_K, our
production default model, as the reference.** No F16 needed — the Q6
artifact we serve IS the thing worth approximating (rel. error of Q6 ≪
Q2 residual). This also makes the result directly deployable: same
weights, same rig, less VRAM.

## Setup

- Reference: heretic-v2 27B Q6_K (22.4 GB file), wikitext-2 PPL(20-chunk)
  = 6.9902; logits saved (20 chunks, 2.5 GB).
- imatrix: 100 chunks wikitext-2 train on the Q6 (llama-imatrix, GPU).
- Bases (imatrix requants of the Q6): Q2_K (10.86 GB), Q3_K_M (13.50 GB).
- Adapter: rank-64 uniform, rsvd (oversampling 32, 4 power iterations,
  fp32 compute — validated against exact SVD on 0.6B: 0.37 vs ~0.38
  captured), F16 factors, streaming extraction (fp32-dequantized 27B is
  ~100 GB; tensors processed one at a time). 554 2D tensors incl. Qwen3.6's
  attn_gate and the MTP/NextN block.

## Baselines measured (PPL on 20 chunks; KLD/top-1 vs Q6 logits; VRAM &
decode at -c 8192, greedy 256 tok, single stream, RTX 5090)

| config | file GB | PPL20 | mean KLD | top-1 | VRAM MiB | decode tok/s |
|---|---|---|---|---|---|---|
| Q6_K (reference) | 22.4 | 6.990 | 0 | 100% | 23567 | 61.0 |
| Q2_K-imat | 10.86 | 7.891 | 0.1529 | 83.6% | 12965 | 99.7 |
| Q3_K_M-imat | 13.50 | 7.243 | 0.0555 | 90.1% | 15311 | 89.2 |

Two structural findings already:
1. **27B tolerates 2-bit far better than 0.6B** (KLD 0.153 vs 0.766) —
   less headroom for the correction, but the bar to beat (Q3_K_M at
   +2.6 GB) is also lower.
2. **The bandwidth inversion is real and measured**: Q2_K decodes 63%
   FASTER than Q6 (99.7 vs 61.0 tok/s). VRAM reduction is a speed BONUS,
   not a tax — the whole "trade compute time for memory" framing inverts;
   only quality is traded, and the adapter buys it back.

## The matchup

Q2_K + r64-F16 adapter (≈ +1.0 GB → ≈ 11.9 GB total, −1.6 GB vs Q3_K_M):
does it beat Q3_K_M's KLD 0.0555 / 90.1% top-1? Decision gates:
- **WIN** (KLD < 0.0555 at fewer bytes): scale hypothesis confirmed →
  draft upstream issue per the PR staging plan.
- **PARTIAL** (0.0555–0.153): correction adds real quality at fewer bytes
  than Q3_K_M; optimize levers (per-layer allocation, Q8 factors,
  full-covariance whitening) before the upstream conversation.
- **NULL** (≥0.153-ish): diagonal whitening doesn't transfer to 27B —
  document, then attack with full covariance.

## Result — 2026-08-03 night: NULL at 27B for diagonal-whitened r64, and
## the mechanism is now measured

Adapter: 870 MB F16, **+0.29 bpw** (the byte-economics prediction
confirmed: 1.47 bpw at 0.6B → 0.29 at 27B for the same rank). 400/554
tensors corrected (154 skipped — no imatrix entries: the MTP/NextN block
and other tensors the calibration forward pass never touches).

| config | file GB | PPL20 | mean KLD | top-1 | VRAM MiB | tok/s |
|---|---|---|---|---|---|---|
| Q6_K (reference) | 22.4 | 6.990 | 0 | 100% | 23567 | 61.0 |
| Q2_K-imat | 10.86 | 7.891 | 0.1529 | 83.6% | 12965 | 99.7 |
| **Q2_K + r64** | **11.73** | **7.869** | **0.1460** | **84.4%** | 13800 | 66.5 |
| Q3_K_M-imat | 13.50 | 7.243 | 0.0555 | 90.1% | 15311 | 89.2 |

The correction moved KLD by only −4.5% (0.153→0.146) and top-1 by +0.8 pt.
Nowhere near Q3_K_M. **Named null: diagonal whitening at uniform rank 64
does not transfer to 27B.**

**The mechanism, caught red-handed:** mean whitened-energy captured
collapsed from 0.37 (0.6B, r64) to **0.07** (27B, r64). Two scale effects
fight: bytes-per-rank improves with d (r(m+n) vs mn), but
capture-per-rank DECLINES with d (r/min(m,n) = 6% of dims at 0.6B vs
1.25% at 27B), and the capture loss wins decisively. Scaling r with d to
hold capture constant destroys the byte economics — there is no free
lunch through uniform rank.

**What survives, precisely:**
1. The 27B ladder measurements themselves are operationally valuable:
   our production Q6 → Q2_K-imat is a 45% VRAM cut AND 63% decode
   speedup at 83.6% top-1; Q3_K_M is 32% off at 90.1%. (The bandwidth
   inversion held at every point measured.)
2. The pipeline (streaming extraction, rsvd, LoRA-form serving) is
   validated at production scale — 269 s to extract a 27B adapter.
3. The next levers are now forced moves, not choices: (a) **rank
   allocation** — capture is wildly uneven across tensors, so 870 MB
   spent uniformly is mostly wasted; spend it only where capture is high;
   (b) **full-covariance whitening** (QERA-exact) — the known fix
   exactly where diagonal fails, and 27B at 2.5 bpw effective is deep in
   its winning regime; (c) correct the tensors Q2_K damages most, not all
   equally.
4. Skipped-tensor gap: MTP/NextN tensors get no imatrix from
   llama-imatrix's standard pass — a correction (or even measurement) gap
   for MTP-preserved models. (Review 2026-08-04: independently found;
   already reported upstream with open PRs — #23476/#23575. Comment
   there, don't file new.)
