#!/usr/bin/env python3
"""E33 / ABLATION-PLAN T1.10 — collect (scale, arch, tensor, d_in, d_out, r,
whitener, base, capture) points for the capture-vs-width regression.

CPU ONLY. No GPU, no llama.cpp binaries: GGUF tensors are dequantized with
gguf-py (numpy) and spectra are computed with numpy/LAPACK.

Why this recomputes instead of reading data/cache27b:
  data/cache27b{,-iq2}/*.npz hold only the top-K (128/192) whitened squared
  singular values `s2` — NO total ||R·D||_F², so capture = top-r/total is
  not computable from them; and the pair that produced them (legacy Q6_K
  reference − legacy Q2_K-imat, E04c-era) is pruned from disk (E31 PREREG
  says the same). Every BF16-provenance pair IS on disk, so spectra are
  recomputed exactly here, with the SAME whitening conventions as
  experiments/04-served-v0/extract_adapter.py (imported, not copied):
    diag : D = sqrt(max(imatrix, 1e-8·max)),  Rd = R·D
    fc   : per-Gram Cholesky (damp 1e-2·mean-diag), block-diagonal when the
           capture was blocked (ffn_down at 27B: 8×2176), Rd = R·L
    none : raw residual (no whitening) — reference row
  capture(r) = Σ_{i≤r} s_i² / ||Rd||_F²   (denominator exact; numerator from
  a full SVD when min(m,n) ≤ 4096, else a randomized range finder with
  K = 256 + 32 oversampling and q = 4 power iterations, s from the exact
  small Gram — validated against full SVDs by `verify-rsvd`).

Usage:
  collect_points.py list
  collect_points.py compute <config> [--workers 8] [--threads 4]
  collect_points.py verify-rsvd <config> [--n 6]
  collect_points.py assemble           -> points.csv, points_log.csv,
                                          aggregate_points.csv
Spectra cache: spectra/<config>/<tensor>.npz (resume-safe).

Scope (coordinator 2026-09-11): the ABLATION-PLAN "Muon-provenance axis"
rider is CLOSED by E30 (experiments/30-muon-spectrum/PREREG.md verdict:
no spectrum flattening on the Qwen3.8-vs-3.6 27B pair, stable-rank
log-ratio +0.0015 ± 0.0035, z=+0.4, n=256) — optimizer provenance is NOT a
covariate here; the two 27B models are compared as (architecture-identical,
same-d) lanes only.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# BLAS thread cap must precede numpy's first import (workers are forked)
_thr = sys.argv[sys.argv.index("--threads") + 1] if "--threads" in sys.argv else "4"
os.environ.setdefault("OPENBLAS_NUM_THREADS", _thr)
os.environ.setdefault("OMP_NUM_THREADS", _thr)
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT_OB = Path("/home/max/Documents/openbeast")
ROOT_RS = Path("/home/max/Documents/openbeast-research")
DATA = ROOT_OB / "research/lowrank/data"
EXP = ROOT_OB / "research/lowrank/experiments"
W = ROOT_OB / "weights"
# bulk outputs live OUTSIDE git (DATA-LOCATION.md): spectra/ + points.csv
BULK = DATA / "spectra-e33"
SPECTRA = BULK / "spectra"
RANKS = [16, 32, 48, 64, 96, 128, 160, 192, 224, 256]
KMAX = 256
OVERSAMPLE = 32
POWER_ITERS = 4

# ---------------------------------------------------------------- configs
# label -> dict(scale, model, arch, ref=[...], quant, base, imatrix, gram_dir,
#               note). d_model in the note only for the reader; d_in/d_out
#               are taken from tensor shapes.
CONFIGS = {
    # ---- 0.6B Qwen3 (classic dense, d=1024, ffn 3072) --------------------
    "q3-06b-Q2K-imat": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "04-served-v0/qwen3-0.6b-Q2_K-imat.gguf", base="Q2_K-imat",
        imatrix=DATA / "gram06b/diag.imatrix.gguf", gram_dir=DATA / "gram06b",
        note="the §4.2 0.37 lane (E04c) — Q8_0 ref (legacy provenance)"),
    "q3-06b-Q2K-imat-legacyim": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "04-served-v0/qwen3-0.6b-Q2_K-imat.gguf", base="Q2_K-imat",
        imatrix=DATA / "qwen3-0.6b.imatrix.gguf", gram_dir=None,
        note="diag with the E04-era imatrix (reproduces the 0.37 exactly?)"),
    "q3-06b-Q2K-noimat": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "03-residual-rank/qwen3-0.6b-Q2_K.gguf", base="Q2_K",
        imatrix=DATA / "gram06b/diag.imatrix.gguf", gram_dir=DATA / "gram06b",
        note="E03 non-imatrix Q2_K"),
    "q3-06b-fcalt2": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "11-alternation/qwen06b-Q2K-fcalt2.gguf", base="Q2_K-fcalt2",
        imatrix=DATA / "gram06b/diag.imatrix.gguf", gram_dir=DATA / "gram06b",
        note="E21 rank-sweep base (crown recipe) — the 'cached E21 spectra'"),
    "q3-06b-IQ2M": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "04-served-v0/qwen3-0.6b-IQ2_M.gguf", base="IQ2_M",
        imatrix=DATA / "gram06b/diag.imatrix.gguf", gram_dir=DATA / "gram06b"),
    "q3-06b-Q3KM": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "04-served-v0/qwen3-0.6b-Q3_K_M.gguf", base="Q3_K_M",
        imatrix=DATA / "gram06b/diag.imatrix.gguf", gram_dir=DATA / "gram06b"),
    "q3-06b-Q4KM": dict(
        scale="0.6B", model="Qwen3-0.6B", arch="qwen3", d_model=1024,
        ref=[W / "Qwen3-0.6B-Q8_0.gguf"],
        quant=EXP / "04-served-v0/qwen3-0.6b-Q4_K_M.gguf", base="Q4_K_M",
        imatrix=DATA / "gram06b/diag.imatrix.gguf", gram_dir=DATA / "gram06b"),
    # ---- 0.8B Qwen3.5 (hybrid GDN mini-sibling, d=1024, ffn 3584) --------
    "q35-08b-Q2K": dict(
        scale="0.8B", model="Qwen3.5-0.8B", arch="qwen35", d_model=1024,
        ref=[W / "research-staging/Qwen3.5-0.8B-BF16.gguf"],
        quant=EXP / "22-qwen35-08b/q35-08b-Q2_K.gguf", base="Q2_K",
        imatrix=DATA / "gram08b/diag.imatrix.gguf", gram_dir=DATA / "gram08b",
        note="E22 BF16-pure lane"),
    "q35-08b-Q2K-rrinput": dict(
        scale="0.8B", model="Qwen3.5-0.8B", arch="qwen35", d_model=1024,
        ref=[W / "research-staging/Qwen3.5-0.8B-BF16.gguf"],
        quant=EXP / "24-yaqa-lite/q35-08b-Q2K-rr-input.gguf", base="Q2_K-rr",
        imatrix=DATA / "gram08b/diag.imatrix.gguf", gram_dir=DATA / "gram08b",
        note="E24 extract-fc.log cross-check (fc r128)"),
    "q35-08b-Q3KM": dict(
        scale="0.8B", model="Qwen3.5-0.8B", arch="qwen35", d_model=1024,
        ref=[W / "research-staging/Qwen3.5-0.8B-BF16.gguf"],
        quant=EXP / "22-qwen35-08b/q35-08b-Q3_K_M.gguf", base="Q3_K_M",
        imatrix=DATA / "gram08b/diag.imatrix.gguf", gram_dir=DATA / "gram08b"),
    "q35-08b-IQ3XXS": dict(
        scale="0.8B", model="Qwen3.5-0.8B", arch="qwen35", d_model=1024,
        ref=[W / "research-staging/Qwen3.5-0.8B-BF16.gguf"],
        quant=EXP / "22-qwen35-08b/q35-08b-IQ3_XXS.gguf", base="IQ3_XXS",
        imatrix=DATA / "gram08b/diag.imatrix.gguf", gram_dir=DATA / "gram08b"),
    "q35-08b-Q4KM": dict(
        scale="0.8B", model="Qwen3.5-0.8B", arch="qwen35", d_model=1024,
        ref=[W / "research-staging/Qwen3.5-0.8B-BF16.gguf"],
        quant=EXP / "22-qwen35-08b/q35-08b-Q4_K_M.gguf", base="Q4_K_M",
        imatrix=DATA / "gram08b/diag.imatrix.gguf", gram_dir=DATA / "gram08b"),
    "q35-08b-NVFP4": dict(
        scale="0.8B", model="Qwen3.5-0.8B", arch="qwen35", d_model=1024,
        ref=[W / "research-staging/Qwen3.5-0.8B-BF16.gguf"],
        quant=W / "research-staging/e16-08b/Qwen3.5-0.8B-NVFP4all.gguf",
        base="NVFP4",
        imatrix=DATA / "gram08b/diag.imatrix.gguf", gram_dir=DATA / "gram08b"),
    # ---- 27B heretic (Qwen3.6-27B-uncensored-heretic-v2, qwen35, d=5120) --
    "h27b-Q2K": dict(
        scale="27B", model="heretic-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf"],
        quant=EXP / "27-bf16-rederivation/h27bf16-Q2_K.gguf", base="Q2_K",
        imatrix=DATA / "gram27b-bf16/diag.imatrix.gguf",
        gram_dir=DATA / "gram27b-bf16",
        note="E27 from-BF16 Q2_K (the BF16-provenance sibling of the §4.2 0.07 lane)"),
    "h27b-MIXED": dict(
        scale="27B", model="heretic-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf"],
        quant=EXP / "27-bf16-rederivation/h27bf16-MIXED.gguf", base="MIXED",
        imatrix=DATA / "gram27b-bf16/diag.imatrix.gguf",
        gram_dir=DATA / "gram27b-bf16",
        note="E27 flagship base (the c≈16.8 fc r128 0.42 lane)"),
    "h27b-IQ3XS": dict(
        scale="27B", model="heretic-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf"],
        quant=EXP / "27-bf16-rederivation/h27bf16-IQ3_XS.gguf", base="IQ3_XS",
        imatrix=DATA / "gram27b-bf16/diag.imatrix.gguf",
        gram_dir=DATA / "gram27b-bf16"),
    # ---- 27B Qwen3.8 (third model, qwen35, d=5120; E32 lane) ---------------
    "q38-27b-UD-Q2KXL": dict(
        scale="27B", model="Qwen3.8-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf",
             W / "research-staging/BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf"],
        quant=W / "research-staging/Qwen3.8-27B-UD-Q2_K_XL.gguf", base="UD-Q2_K_XL",
        imatrix=DATA / "imatrix-38-bf16.gguf", gram_dir=DATA / "gram38-bf16",
        note="E32 extract-UD-Q2_K_XL.log cross-check"),
    "q38-27b-Q2K-ours": dict(
        scale="27B", model="Qwen3.8-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf",
             W / "research-staging/BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf"],
        quant=W / "research-staging/e33/q38-27b-bf16-Q2_K.gguf", base="Q2_K",
        imatrix=DATA / "imatrix-38-bf16.gguf", gram_dir=DATA / "gram38-bf16",
        note="ADDENDUM model-vs-base: SAME recipe as h27b-Q2K (one llama-quantize "
             "step from BF16, our 48-chunk imatrix, MTP pin blk.64=q5_k)"),
    "q38-27b-UD-IQ2S": dict(
        scale="27B", model="Qwen3.8-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf",
             W / "research-staging/BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf"],
        quant=W / "research-staging/Qwen3.8-27B-UD-IQ2_S.gguf", base="UD-IQ2_S",
        imatrix=DATA / "imatrix-38-bf16.gguf", gram_dir=DATA / "gram38-bf16"),
    "q38-27b-UD-IQ3S": dict(
        scale="27B", model="Qwen3.8-27B", arch="qwen35", d_model=5120,
        ref=[W / "research-staging/BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf",
             W / "research-staging/BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf"],
        quant=W / "research-staging/Qwen3.8-27B-UD-IQ3_S.gguf", base="UD-IQ3_S",
        imatrix=DATA / "imatrix-38-bf16.gguf", gram_dir=DATA / "gram38-bf16"),
}

# per-tensor capture logs (extract_adapter.py stdout) -> log-derived points
LOGS = [
    dict(path=EXP / "32-t117-gsq-head-to-head/extract-UD-Q2_K_XL.log",
         config="q38-27b-UD-Q2KXL", whitener="fc", r=128),
    dict(path=EXP / "32-t117-gsq-head-to-head/extract-UD-IQ2_S.log",
         config="q38-27b-UD-IQ2S", whitener="fc", r=128),
    dict(path=EXP / "32-t117-gsq-head-to-head/extract-UD-IQ3_S.log",
         config="q38-27b-UD-IQ3S", whitener="fc", r=128),
    dict(path=ROOT_RS / "research/lowrank/experiments/24-yaqa-lite/extract-fc.log",
         config="q35-08b-Q2K-rrinput", whitener="fc", r=128),
    # E34 (coordinator note 2026-09-11): fresh 0.8B fc r128 points, gram08b
    dict(path=EXP / "34-e16-nvfp4-08b/extract-nvfp4all.log",
         config="q35-08b-NVFP4", whitener="fc", r=128),
    dict(path=EXP / "34-e16-nvfp4-08b/extract-q3km.log",
         config="q35-08b-Q3KM", whitener="fc", r=128),
]

# mean-over-tensor numbers quoted in the paper/rollup (NOT per-tensor; used
# only as cross-checks in REPORT.md, never in the fit)
AGGREGATE = [
    ("0.6B", "Qwen3-0.6B", "Q2_K-imat", "diag", 64, 0.37, 1024,
     "JOURNAL.md 'capture ≈ c·(r/d)' + E04c; paper §4.2"),
    ("0.6B", "Qwen3-0.6B", "Q2_K-imat", "fc", 64, 0.60, 1024,
     "experiments/10-full-covariance/README.md checkpoint table"),
    ("27B", "heretic-27B", "Q2_K-imat(legacy, Q6 ref)", "diag", 64, 0.07, 5120,
     "experiments/04b-27b/README.md; paper §4.2 (pair pruned from disk)"),
    ("27B", "heretic-27B", "MIXED", "fc", 128, 0.42, 5120,
     "RESULTS_ROLLUP.md / adversarial-stats §F6 (c≈16.8)"),
    ("27B", "heretic-27B", "NVFP4", "fc", 128, 0.52, 5120,
     "RESULTS_ROLLUP.md E16 row (c≈20.8)"),
    ("0.8B", "Qwen3.5-0.8B", "Q2_K-rr", "fc", 128, 0.49, 1024,
     "experiments/24-yaqa-lite/extract-fc.log mean line"),
    ("27B", "Qwen3.8-27B", "UD-{IQ2_S,Q2_K_XL,IQ3_S}", "fc", 128, 0.21, 5120,
     "experiments/32-t117-gsq-head-to-head/extract-UD-*.log mean line"),
]

KIND_PATTERNS = [
    (r"attn_q\.", "attn_q"), (r"attn_k\.", "attn_k"), (r"attn_v\.", "attn_v"),
    (r"attn_output\.", "attn_o"), (r"attn_qkv\.", "attn_qkv"),
    (r"attn_gate\.", "attn_gate"), (r"ssm_out\.", "ssm_out"),
    (r"ffn_gate\.", "ffn_gate"), (r"ffn_up\.", "ffn_up"),
    (r"ffn_down\.", "ffn_down"),
]


def kind_of(name):
    for pat, k in KIND_PATTERNS:
        if re.search(pat, name):
            return k
    return "other"


def layer_of(name):
    m = re.match(r"blk\.(\d+)\.", name)
    return int(m.group(1)) if m else -1


# ------------------------------------------------------------ numerics
def _orth(Y):
    """Orthonormal basis of range(Y) via the small Gram (float64), twice
    (CholQR2-style): robust to the ill-conditioning power iteration builds."""
    for _ in range(2):
        G = Y.T.astype(np.float64) @ Y.astype(np.float64)
        ev, V = np.linalg.eigh(G)
        keep = ev > ev.max() * 1e-14
        Y = (Y.astype(np.float64) @ V[:, keep]) / np.sqrt(ev[keep])[None, :]
    return Y.astype(np.float32)


def top_s2(Rd, kmax=KMAX, exact_max_dim=4096):
    """-> (s2 descending [≤kmax or full], etot exact, method)."""
    etot = float(np.einsum("ij,ij->", Rd, Rd, dtype=np.float64))
    if min(Rd.shape) <= exact_max_dim:
        s = np.linalg.svd(Rd.astype(np.float64), compute_uv=False)
        return (s ** 2), etot, "exact-svd"
    rng = np.random.default_rng(0xBEA57)
    k = min(kmax + OVERSAMPLE, min(Rd.shape))
    wide = Rd.shape[1] > Rd.shape[0]
    M = Rd if not wide else Rd.T          # make M tall: (big, small)
    # range finder on M (small side): Y = M M^T ... acting on M.T columns
    # we want top left-singular subspace of M — same singular values either
    # way, so work with whichever orientation is cheaper: sample Q in the
    # SMALL dimension space and never orthonormalize a (big × k) matrix.
    A = Rd if Rd.shape[0] <= Rd.shape[1] else Rd.T   # (small, big)
    Y = A @ rng.standard_normal((A.shape[1], k), dtype=np.float32)  # (small,k)
    Y = _orth(Y)
    for _ in range(POWER_ITERS):
        Y = _orth(A @ (A.T @ Y))
    B = (A.T @ Y).astype(np.float64)       # (big, k) = A^T Q
    ev = np.linalg.eigvalsh(B.T @ B)       # = s² of Q^T A (exact for the
    s2 = np.sort(ev)[::-1][:kmax]          #   projected matrix)
    return np.maximum(s2, 0.0), etot, f"rsvd(k={kmax}+{OVERSAMPLE},q={POWER_ITERS})"


# ------------------------------------------------------------ worker
_G = {}


def _init(cfg_label, threads):
    sys.path.insert(0, str(ROOT_OB / "llama.cpp/gguf-py"))
    sys.path.insert(0, str(ROOT_RS / "research/lowrank/experiments/04-served-v0"))
    from extract_adapter import deq, index_2d, load_imatrix, gram_whitener
    from gguf import GGUFReader

    def index_2d_tolerant(path, min_dim=64):
        # split-GGUF shards after the first carry no general.architecture
        out = {}
        for t in GGUFReader(path).tensors:
            shape = [int(d) for d in t.shape if int(d) > 1]
            if len(shape) == 2 and min(shape) >= min_dim:
                out[t.name] = t
        return out

    cfg = CONFIGS[cfg_label]
    ref = {}
    for p in cfg["ref"]:
        ref.update(index_2d_tolerant(str(p)))
    qnt = index_2d_tolerant(str(cfg["quant"]))
    _G.update(cfg=cfg, ref=ref, qnt=qnt, deq=deq,
              imat=load_imatrix(str(cfg["imatrix"])),
              gram_man=(json.load(open(cfg["gram_dir"] / "grams.json"))
                        if cfg["gram_dir"] else None),
              gram_whitener=gram_whitener, out=SPECTRA / cfg_label)


def _one(name):
    try:
        return _one_inner(name)
    except Exception as e:  # keep the pool alive; report per tensor
        return name, f"ERROR {type(e).__name__}: {e}"


def _one_inner(name):
    cfg, ref, qnt = _G["cfg"], _G["ref"], _G["qnt"]
    fn = _G["out"] / (name + ".npz")
    if fn.exists():
        return name, "cached"
    t0 = time.time()
    w_ref = _G["deq"](ref[name])
    n_out, n_in = w_ref.shape
    m2 = _G["imat"].get(name)
    if m2 is None or len(m2) != n_in:
        return name, "skip(no imatrix)"
    R = (w_ref - _G["deq"](qnt[name])).astype(np.float32)
    del w_ref
    out = dict(m=n_out, n=n_in, kind=kind_of(name), layer=layer_of(name),
               qtype=int(qnt[name].tensor_type))
    # none
    s2, et, meth = top_s2(R)
    out.update(s2_none=s2, etot_none=et, method=meth)
    # diag
    d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
    s2, et, _ = top_s2(R * d[None, :])
    out.update(s2_diag=s2, etot_diag=et)
    # fc
    wh = None
    if _G["gram_man"] is not None:
        wh = _G["gram_whitener"](str(cfg["gram_dir"]), _G["gram_man"], name, n_in)
    if wh is not None:
        Rg = np.empty_like(R)
        for off, L in wh:
            bs = L.shape[0]
            Rg[:, off:off + bs] = R[:, off:off + bs] @ L
        s2, et, _ = top_s2(Rg)
        out.update(s2_fc=s2, etot_fc=et, fc_blocks=len(wh))
    np.savez(fn, **out)
    return name, f"{time.time()-t0:.1f}s"


def cmd_compute(args):
    cfg = CONFIGS[args.config]
    for p in cfg["ref"] + [cfg["quant"], cfg["imatrix"]] + \
            ([cfg["gram_dir"] / "grams.json"] if cfg["gram_dir"] else []):
        if not Path(p).exists():
            sys.exit(f"missing: {p}")
    outdir = SPECTRA / args.config
    outdir.mkdir(parents=True, exist_ok=True)
    _init(args.config, args.threads)
    names = [n for n in _G["ref"] if n in _G["qnt"]]
    print(f"[{args.config}] {len(names)} candidate tensors, "
          f"{args.workers} workers × {args.threads} threads", flush=True)
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing as mp
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(args.workers, initializer=_init,
                             initargs=(args.config, args.threads),
                             mp_context=mp.get_context("fork")) as ex:
        for name, msg in ex.map(_one, names, chunksize=1):
            done += 1
            if done % 25 == 0 or "skip" in msg or "ERROR" in msg:
                print(f"  [{done}/{len(names)}] {name} {msg} "
                      f"({time.time()-t0:.0f}s)", flush=True)
    meta = {k: (str(v) if isinstance(v, Path) else
                [str(x) for x in v] if isinstance(v, list) else v)
            for k, v in cfg.items()}
    (outdir / "config.json").write_text(json.dumps(meta, indent=1))
    print(f"[{args.config}] DONE {time.time()-t0:.0f}s", flush=True)


def cmd_verify(args):
    """Exact full-SVD spectra on a sample of large tensors -> rsvd bias."""
    _init(args.config, args.threads)
    names = [n for n in _G["ref"] if n in _G["qnt"]]
    rng = np.random.default_rng(7)
    pick = [n for n in names if kind_of(n) in ("ffn_down", "ffn_up", "attn_qkv", "ssm_out")]
    pick = list(rng.choice(pick, size=min(args.n, len(pick)), replace=False))
    rows = []
    for name in pick:
        w_ref = _G["deq"](_G["ref"][name]); n_out, n_in = w_ref.shape
        m2 = _G["imat"][name]
        R = (w_ref - _G["deq"](_G["qnt"][name])).astype(np.float32)
        d = np.sqrt(np.maximum(m2, 1e-8 * m2.max())).astype(np.float32)
        Rd = R * d[None, :]
        t = time.time()
        s_ex = np.linalg.svd(Rd.astype(np.float64), compute_uv=False) ** 2
        s_rs, et, _ = top_s2(Rd)
        for r in (32, 64, 128, 256):
            rows.append((name, r, s_ex[:r].sum() / s_ex.sum(),
                         s_rs[:r].sum() / et))
        print(f"{name} exact-svd {time.time()-t:.0f}s  "
              + "  ".join(f"r{r}: {a:.4f}/{b:.4f}" for _, r, a, b in rows[-4:]),
              flush=True)
    with open(HERE / f"verify-rsvd-{args.config}.txt", "w") as f:
        f.write("tensor r cap_exact cap_rsvd\n")
        for row in rows:
            f.write("%s %d %.5f %.5f\n" % row)


# ------------------------------------------------------------ assemble
def cmd_assemble(args):
    import csv
    pts = []
    for label, cfg in CONFIGS.items():
        sd = SPECTRA / label
        if not sd.exists():
            continue
        for fn in sorted(sd.glob("*.npz")):
            z = np.load(fn)
            base_row = dict(
                config=label, scale=cfg["scale"], model=cfg["model"],
                arch=cfg["arch"], d_model=cfg["d_model"], base=cfg["base"],
                tensor=fn.stem, kind=str(z["kind"]), layer=int(z["layer"]),
                d_in=int(z["n"]), d_out=int(z["m"]), method=str(z["method"]),
                provenance=str(fn))
            for wh in ("none", "diag", "fc"):
                if f"s2_{wh}" not in z.files:
                    continue
                s2 = z[f"s2_{wh}"]; et = float(z[f"etot_{wh}"])
                whl = wh
                if wh == "fc" and int(z["fc_blocks"]) > 1:
                    whl = f"fc{int(z['fc_blocks'])}blk"
                for r in RANKS:
                    if r > len(s2):
                        continue
                    pts.append(dict(base_row, whitener=whl, r=r,
                                    capture=float(s2[:r].sum() / et),
                                    source="spectra"))
    cols = ["config", "scale", "model", "arch", "d_model", "base", "tensor",
            "kind", "layer", "d_in", "d_out", "whitener", "r", "capture",
            "method", "source", "provenance"]
    with open(BULK / "points.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for p in pts:
            w.writerow(p)
    print(f"{BULK/'points.csv'}: {len(pts)} rows from "
          f"{len(set(p['config'] for p in pts))} configs")

    # log-derived points (need shapes: take from the spectra cache if present,
    # else from the quant gguf)
    lpts = []
    for L in LOGS:
        if not L["path"].exists():
            print("log missing:", L["path"]); continue
        cfg = CONFIGS[L["config"]]
        shapes = {}
        sd = SPECTRA / L["config"]
        if sd.exists():
            for fn in sd.glob("*.npz"):
                z = np.load(fn); shapes[fn.stem] = (int(z["m"]), int(z["n"]))
        if not shapes:
            sys.path.insert(0, str(ROOT_OB / "llama.cpp/gguf-py"))
            sys.path.insert(0, str(ROOT_RS / "research/lowrank/experiments/04-served-v0"))
            from extract_adapter import index_2d
            q, _ = index_2d(str(cfg["quant"]))
            for n, t in q.items():
                sh = [int(d) for d in t.shape if int(d) > 1]
                shapes[n] = (sh[1], sh[0])
        seen = set()
        for line in open(L["path"]):
            m = re.match(r"(\S+)\s+r=(\d+)\s+(?:whitened-energy captured|"
                         r"2side-whitened captured|col-patch whitened-energy)"
                         r"\s+([0-9.]+)", line)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            name = m.group(1); mo, ni = shapes.get(name, (-1, -1))
            lpts.append(dict(config=L["config"], scale=cfg["scale"],
                             model=cfg["model"], arch=cfg["arch"],
                             d_model=cfg["d_model"], base=cfg["base"],
                             tensor=name, kind=kind_of(name),
                             layer=layer_of(name), d_in=ni, d_out=mo,
                             whitener=L["whitener"], r=int(m.group(2)),
                             capture=float(m.group(3)),
                             method="extract_adapter.py log (2-dec)",
                             source="log", provenance=str(L["path"])))
    with open(HERE / "points_log.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for p in lpts:
            w.writerow(p)
    print(f"points_log.csv: {len(lpts)} rows")
    # per-tensor cross-check: log capture vs recomputed spectra capture
    # (also decides which whitener a log used: diag vs fc distance)
    sp = {}
    for p_ in pts:
        sp[(p_["config"], p_["tensor"], p_["whitener"].split("blk")[0].rstrip("0123456789"), p_["r"])] = p_["capture"]
    ck = ["config log_whitener n  mean(log-spectra)  max|diff|  [vs fc]   [vs diag]"]
    for L in LOGS:
        for whc in ("fc", "diag"):
            pass
        lp = [p_ for p_ in lpts if p_["config"] == L["config"]]
        res = {}
        for whc in ("fc", "diag"):
            d = [p_["capture"] - sp[(L["config"], p_["tensor"], whc, p_["r"])]
                 for p_ in lp if (L["config"], p_["tensor"], whc, p_["r"]) in sp]
            res[whc] = (len(d), float(np.mean(d)) if d else float("nan"),
                        float(np.max(np.abs(d))) if d else float("nan"))
        ck.append(f"{L['config']:22s} {L['whitener']:4s} n={res['fc'][0]:3d}  "
                  f"vs fc: mean {res['fc'][1]:+.4f} max|d| {res['fc'][2]:.4f}   "
                  f"vs diag: mean {res['diag'][1]:+.4f} max|d| {res['diag'][2]:.4f}")
    (HERE / "log_crosscheck.txt").write_text("\n".join(ck) + "\n")
    print("\n".join(ck))
    with open(HERE / "aggregate_points.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scale", "model", "base", "whitener", "r", "capture_mean",
                    "d_model", "provenance"])
        for row in AGGREGATE:
            w.writerow(row)
    print(f"aggregate_points.csv: {len(AGGREGATE)} rows")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("compute"); c.add_argument("config")
    c.add_argument("--workers", type=int, default=8)
    c.add_argument("--threads", type=int, default=4)
    v = sub.add_parser("verify-rsvd"); v.add_argument("config")
    v.add_argument("--n", type=int, default=6)
    v.add_argument("--threads", type=int, default=16)
    sub.add_parser("assemble")
    args = ap.parse_args()
    if args.cmd == "list":
        for k, v in CONFIGS.items():
            ok = all(Path(p).exists() for p in v["ref"] + [v["quant"], v["imatrix"]])
            print(f"{'ok ' if ok else 'MISSING'} {k:28s} {v['scale']:5s} "
                  f"{v['model']:14s} {v['base']}")
    elif args.cmd == "compute":
        cmd_compute(args)
    elif args.cmd == "verify-rsvd":
        cmd_verify(args)
    else:
        cmd_assemble(args)


if __name__ == "__main__":
    main()
