# E29 — SRR preserve-then-quantize split at 27B: order doesn't matter

**Recon lever L3** (SRR 2602.02001 transplant). Pre-registered JOURNAL
2026-08-11 16:06 (part 1) and 16:32 (part 2); both run same day.

## Question

SRR carves the top-k whitened subspace out of W BEFORE quantizing
(kept as a high-precision low-rank branch); we correct the residual
AFTER quantizing. At equal bytes, does the order matter?

## Part 1 — proxy (e29_proxy.py, results.txt)

12 tensors × {whitened, raw} carve × k ∈ {64,128,256}:

- **Whitened-W capture @k=128: 76.56% mean** — a finding on its own:
  E01's 0.6B raw-SVD "W is energy-full-rank" null does NOT transfer
  to the whitened metric at 27B. The whitened weight matrix is highly
  low-rank-capturable at scale.
- Per-16-block absmax deflation (Q2_K scale proxy): only −4.5%
  (whitened) / −7% (raw) — the quantizer-side mechanism is weak.

Registered gate (capture > 15%) fired → part 2.

## Part 2 — full build (e29_build.py + run_build.sh)

Carve set = exactly the fc adapter's tensors (q/k/v/output ×16,
qkv/gate/ssm_out ×48), k=128 whitened carve per tensor; deflated base
= BF16 copy with carved tensors overwritten in place (RNE bf16),
quantized with the EXACT MIXED recipe (same imatrix, MTP pin) →
srr-MIXED.gguf, byte-identical to MIXED (12162412928). Carve exported
as Q8 LoRA (321 MB — byte-equal to the flagship adapter). Known
approximations disclosed in the registration (original-model imatrix;
Q8 factors).

## Verdict (n=100 paired, kld100-SRR.log — 100 rows verified)

| pair | KLD t | NLL t | top-1 t | verdict |
|---|---|---|---|---|
| SRR vs MIXEDfc | +0.99 | +0.74 | −1.68 | **TIE — order doesn't matter** (registered \|t\|<2 outcome) |
| SRR vs CONTROL | +5.15 | +1.04 | −3.86 | control beats SRR decisively |

**At equal bytes at 27B, preserve-then-quantize ≈ fix-after-quantize —
and mixed-TYPE allocation beats both.** The SRR adapter captures 76%
of *W's* whitened energy; the fc adapter captures ~40% of the
*residual's*; completely different surrogates, same KLD. Together with
E28b (shared-basis, +12% capture, same KLD) this session measured an
equal-byte EQUIVALENCE CLASS of low-rank mechanisms at 27B: the byte
budget, not the mechanism, sets the quality; only TYPE allocation
escapes the class.

Paper placement: §4 walls — cite SRR as must-cite with this measured
tie; the equivalence-class framing is new and ours.

## Reproduction

    ./run_build.sh          # carve+deflate (~9 min) + quantize (71 s)
    ../27-bf16-rederivation/measure100.sh SRR srr-MIXED.gguf --lora srr-carve-r128q8.gguf

Cleanup note: h27bf16-DEFLATED.gguf (55 GB) is a rebuildable
intermediate — delete freely.
