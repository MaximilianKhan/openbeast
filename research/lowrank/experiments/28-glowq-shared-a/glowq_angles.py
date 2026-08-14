#!/usr/bin/env python3
"""E28 / recon L2: GlowQ shared-A test (pre-registered JOURNAL 2026-08-11 15:10).

Do input-sharing tensor groups (attn q/k/v on full-attention layers;
attn_qkv/attn_gate on GDN layers) have overlapping whitened-residual
right-subspaces? If a SHARED A-factor at byte-parity rank r_s captures
at least what separate rank-R factors capture, adapter bytes shrink
with no quality cost (GO -> rebuild E27 byte-fair table).

Byte parity: separate = R * sum_t(n_in + out_t); shared = r_s * (n_in
+ sum_t out_t)  =>  r_s = R * sum_t(n_in + out_t) / (n_in + sum_t out_t).

Cached inputs only: BF16 ref + E27 MIXED base + gram27b-bf16. No GPU.
"""
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "04-served-v0"))
import extract_adapter as ex                      # noqa: E402

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf                                       # noqa: E402
from gguf.quants import dequantize                # noqa: E402

BF16 = REPO / "weights/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf"
MIXED = HERE.parent / "27-bf16-rederivation/h27bf16-MIXED.gguf"
GRAMDIR = REPO / "research/lowrank/data/gram27b-bf16"
OUT = HERE / "results.txt"
R = 128                       # the flagship adapter's per-tensor rank
GDN_SAMPLE_STRIDE = 4         # every 4th GDN layer (verdict sample; byte math is exact)


def tmap(path):
    r = gguf.GGUFReader(str(path))
    return {t.name: t for t in r.tensors if len(t.shape) == 2}


def deq(t):
    return dequantize(t.data, t.tensor_type).astype(np.float32)


def rsvd_top(M, r, p=16, iters=4, seed=0):
    """Top-r singular values + right singular vectors (rows) of M."""
    rng = np.random.default_rng(seed)
    k = min(r + p, min(M.shape))
    Y = M @ rng.standard_normal((M.shape[1], k)).astype(np.float32)
    Y, _ = np.linalg.qr(Y)
    for _ in range(iters):
        Y = M @ (M.T @ Y)
        Y, _ = np.linalg.qr(Y)
    B = Y.T @ M
    _, s, Vt = np.linalg.svd(B, full_matrices=False)
    return s[:r].astype(np.float64), Vt[:r]


def main():
    ref, qnt = tmap(BF16), tmap(MIXED)
    man = ex.load_grams(GRAMDIR)

    attn_layers = sorted(int(n.split(".")[1]) for n in ref
                         if n.endswith("attn_k.weight") and "blk." in n
                         and int(n.split(".")[1]) < 64)
    gdn_layers = sorted(int(n.split(".")[1]) for n in ref
                        if n.endswith("attn_qkv.weight") and "blk." in n
                        and int(n.split(".")[1]) < 64)
    gdn_sample = gdn_layers[::GDN_SAMPLE_STRIDE]
    groups = [("attn", i, ["attn_q", "attn_k", "attn_v"]) for i in attn_layers]
    groups += [("gdn", i, ["attn_qkv", "attn_gate"]) for i in gdn_sample]

    res = open(OUT, "a")
    res.write(f"# E28 run {time.strftime('%F %T')}  R={R} "
              f"attn_layers={len(attn_layers)} gdn_sample={len(gdn_sample)}"
              f"/{len(gdn_layers)}\n")
    res.write("# group blk r_s  sep_cap  shared_cap@r_s  ratio  "
              "shared_cap@R  mean_cos2_pairs  E_total\n")
    agg = {}
    for kind, blk, members in groups:
        t0 = time.time()
        names = [f"blk.{blk}.{m}.weight" for m in members]
        if any(n not in ref or n not in qnt for n in names):
            res.write(f"{kind} {blk} SKIP missing tensors\n")
            continue
        gram_name = names[1] if kind == "attn" else names[0]
        wh = ex.gram_whitener(str(GRAMDIR), man, gram_name, 5120)
        if wh is None or len(wh) != 1:
            res.write(f"{kind} {blk} SKIP no single-block gram\n")
            continue
        L = wh[0][1]
        Ms, caps, Es, Vs, outs = [], [], [], [], []
        for n in names:
            w = deq(ref[n])
            Rres = w - deq(qnt[n])
            del w
            assert Rres.shape[1] == 5120, (n, Rres.shape)
            M = Rres @ L
            del Rres
            s, Vt = rsvd_top(M, R)
            Ms.append(M)
            caps.append(float((s ** 2).sum()))
            Es.append(float((M.astype(np.float64) ** 2).sum()))
            Vs.append(Vt)
            outs.append(M.shape[0])
        n_in = 5120
        r_s = int(round(R * sum(n_in + o for o in outs)
                        / (n_in + sum(outs))))
        Mstack = np.vstack(Ms)
        _, Vs_shared = rsvd_top(Mstack, r_s, seed=1)
        del Mstack
        shared_rs = sum(float((np.linalg.norm(M @ Vs_shared.T) ** 2))
                        for M in Ms)
        shared_R = sum(float(np.linalg.norm(M @ Vs_shared[:R].T) ** 2)
                       for M in Ms)
        cos2 = []
        for a in range(len(Vs)):
            for b in range(a + 1, len(Vs)):
                sv = np.linalg.svd(Vs[a] @ Vs[b].T, compute_uv=False)
                cos2.append(float((sv ** 2).mean()))
        sep = sum(caps)
        ratio = shared_rs / sep if sep > 0 else float("nan")
        row = (f"{kind} {blk} r_s={r_s} sep={sep:.4e} "
               f"shared_rs={shared_rs:.4e} ratio={ratio:.4f} "
               f"shared_R={shared_R:.4e} cos2={np.mean(cos2):.4f} "
               f"E={sum(Es):.4e} t={time.time()-t0:.0f}s")
        res.write(row + "\n")
        res.flush()
        print(row, flush=True)
        a = agg.setdefault(kind, [0.0, 0.0, 0.0])
        a[0] += sep
        a[1] += shared_rs
        a[2] += sum(Es)
        del Ms, Vs
    for kind, (sep, sh, e) in agg.items():
        line = (f"AGG {kind}: sep_cap={sep:.4e} shared_cap={sh:.4e} "
                f"RATIO={sh/sep:.4f} total_E={e:.4e}")
        res.write(line + "\n")
        print(line, flush=True)
    res.write("# done %s\n" % time.strftime("%F %T"))
    res.close()


if __name__ == "__main__":
    main()
