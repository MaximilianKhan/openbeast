#!/usr/bin/env python3
"""E28b: build the shared-A adapter (GlowQ lever, GO per E28).

Groups ({attn_q,attn_k,attn_v} x16 attn layers; {attn_qkv,attn_gate}
x48 GDN layers) get a SHARED whitened right-factor at byte-parity rank
(rounded to %32 for Q8_0): correction C_t = B_t . A with
A = V . L^-1 (shared), B_t = (R_t L) V^T (per tensor). For the exact
per-group SVD this equals the one-sided whitened optimum; sharing V
across the group is E28's measured lever. Singles (ssm_out,
attn_output) are copied from the flagship adapter (Q8 bytes,
rank-fold x128 applied — lossless: Q8 scale x pow2).

File stores A per tensor (stock LoRA GGUF has no cross-tensor
aliasing) — the FILE is inflated vs the dedup accounting; the printed
DEDUP-BYTES line is the honest serving cost with a loader-side alias
patch (same local-patch class as the fused kernel). KLD from this file
is valid either way: the correction math is identical.
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
from gguf.quants import dequantize, quantize      # noqa: E402

BF16 = REPO / "weights/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf"
MIXED = HERE.parent / "27-bf16-rederivation/h27bf16-MIXED.gguf"
OLD_ADAPTER = HERE.parent / "27-bf16-rederivation/mixed-fc-r128q8-bf16.gguf"
GRAMDIR = REPO / "research/lowrank/data/gram27b-bf16"
R128_MODE = len(sys.argv) > 1 and sys.argv[1] == "r128"
OUT = HERE.parent / ("27-bf16-rederivation/mixed-fc-sharedA-r128-q8-bf16.gguf"
                     if R128_MODE else
                     "27-bf16-rederivation/mixed-fc-sharedA-q8-bf16.gguf")
R0 = 128
QT = gguf.GGMLQuantizationType.Q8_0


def deq(t):
    return dequantize(t.data, t.tensor_type).astype(np.float32)


def rsvd_rows(M, r, seed=0xBEA57):
    rng = np.random.default_rng(seed)
    k = min(r + 32, min(M.shape))
    Y = M @ rng.standard_normal((M.shape[1], k)).astype(np.float32)
    Y, _ = np.linalg.qr(Y)
    for _ in range(4):
        Y = M @ (M.T @ Y)
        Y, _ = np.linalg.qr(Y)
    B = Y.T @ M
    _, s, Vt = np.linalg.svd(B, full_matrices=False)
    return s[:r].astype(np.float64), Vt[:r]


def q8(w, name, arr):
    w.add_tensor(name, quantize(np.ascontiguousarray(arr, dtype=np.float32),
                                QT), raw_dtype=QT)


def main():
    t_all = time.time()
    ref, arch = ex.index_2d(str(BF16))
    qnt, _ = ex.index_2d(str(MIXED))
    rold = gguf.GGUFReader(str(OLD_ADAPTER))
    old = {t.name: t for t in rold.tensors}
    man = ex.load_grams(str(GRAMDIR))

    attn_layers = sorted(int(n.split(".")[1]) for n in ref
                         if n.endswith("attn_k.weight") and int(n.split(".")[1]) < 64)
    gdn_layers = sorted(int(n.split(".")[1]) for n in ref
                        if n.endswith("attn_qkv.weight") and int(n.split(".")[1]) < 64)

    w = gguf.GGUFWriter(str(OUT), arch)
    w.add_type(gguf.GGUFType.ADAPTER)
    w.add_string(gguf.Keys.Adapter.TYPE, "lora")
    w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, 1.0)   # mixed ranks: fold r into A

    file_bytes = {"shared_dup": 0, "dedup": 0, "old_equiv": 0}

    def group(blk, members, r_s, gram_key):
        names = [f"blk.{blk}.{m}.weight" for m in members]
        wh = ex.gram_whitener(str(GRAMDIR), man, gram_key, 5120)
        assert wh is not None and len(wh) == 1, (blk, gram_key)
        L = wh[0][1].astype(np.float32)
        Ms, outs = [], []
        for n in names:
            Rres = deq(ref[n]) - deq(qnt[n])
            assert Rres.shape[1] == 5120, (n, Rres.shape)
            Ms.append(Rres @ L)
            outs.append(Rres.shape[0])
            del Rres
        s, V = rsvd_rows(np.vstack(Ms), r_s)
        A = np.linalg.solve(L.T.astype(np.float64), V.T.astype(np.float64)).T
        cap = 0.0
        tot = 0.0
        for n, M in zip(names, Ms):
            Bt = (M @ V.T).astype(np.float64)
            cap += float((Bt ** 2).sum())
            tot += float((M.astype(np.float64) ** 2).sum())
            bn = np.linalg.norm(Bt, axis=0)
            an = np.linalg.norm(A, axis=1)
            eta = np.sqrt(np.maximum(bn, 1e-30) / np.maximum(an, 1e-30))
            Bt2 = Bt / eta[None, :]
            At = (A * eta[:, None]) * r_s          # fold rank (alpha=1)
            q8(w, n + ".lora_a", At)
            q8(w, n + ".lora_b", Bt2)
        per = 34 / 32
        file_bytes["shared_dup"] += int(per * (len(names) * r_s * 5120
                                               + r_s * sum(outs)))
        file_bytes["dedup"] += int(per * (r_s * 5120 + r_s * sum(outs)))
        file_bytes["old_equiv"] += int(per * (len(names) * R0 * 5120
                                              + R0 * sum(outs)))
        print(f"blk.{blk} {'/'.join(members)} r_s={r_s} "
              f"capture={cap/max(tot,1e-30):.3f} outs={outs}", flush=True)

    # byte-parity ranks, %32 for Q8_0; dims fetched from real tensors
    def parity_rank(blk, members):
        outs = []
        for m in members:
            t = ref[f"blk.{blk}.{m}.weight"]
            sh = list(t.shape)
            outs.append(int(sh[1]) if int(sh[0]) == 5120 else int(sh[0]))
        exact = R0 * sum(5120 + o for o in outs) / (5120 + sum(outs))
        return int(round(exact / 32) * 32), exact

    if R128_MODE:
        # E28c bytes-variant: shared A at the ORIGINAL rank — quality
        # predicted ~tie (E28: 99.55%/99.75% same-rank capture), dedup
        # bytes -17% vs the flagship adapter.
        r_att = r_gdn = R0
        print(f"r128 bytes-variant -> {OUT.name}", flush=True)
    else:
        r_att, e_att = parity_rank(attn_layers[0],
                                   ["attn_q", "attn_k", "attn_v"])
        r_gdn, e_gdn = parity_rank(gdn_layers[0], ["attn_qkv", "attn_gate"])
        print(f"parity ranks: attn {e_att:.1f} -> {r_att}, "
              f"gdn {e_gdn:.1f} -> {r_gdn}", flush=True)

    for blk in attn_layers:
        group(blk, ["attn_q", "attn_k", "attn_v"], r_att,
              f"blk.{blk}.attn_k.weight")
    for blk in gdn_layers:
        group(blk, ["attn_qkv", "attn_gate"], r_gdn,
              f"blk.{blk}.attn_qkv.weight")

    # singles: copy Q8 payloads from the flagship adapter, fold x128
    n_singles = 0
    for name, t in old.items():
        base = name.replace(".lora_a", "").replace(".lora_b", "")
        if not (base.endswith("ssm_out.weight")
                or base.endswith("attn_output.weight")):
            continue
        arr = dequantize(t.data, t.tensor_type).astype(np.float32)
        if name.endswith(".lora_a"):
            arr = arr * R0
        q8(w, name, arr)
        n_singles += 1
        per = 34 / 32
        file_bytes["shared_dup"] += int(per * arr.size)
        file_bytes["dedup"] += int(per * arr.size)
        file_bytes["old_equiv"] += int(per * arr.size)

    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    size = OUT.stat().st_size
    print(f"\nwrote {OUT.name}: {size/1e6:.1f} MB file "
          f"({n_singles} single tensors copied)")
    print(f"BYTES  file(dup-A)={file_bytes['shared_dup']/1e6:.1f}MB  "
          f"DEDUP(alias-patch)={file_bytes['dedup']/1e6:.1f}MB  "
          f"old-equiv@r128={file_bytes['old_equiv']/1e6:.1f}MB")
    print(f"total {time.time()-t_all:.0f}s")


if __name__ == "__main__":
    main()
