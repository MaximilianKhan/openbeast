#!/usr/bin/env python3
"""E29 part 2: SRR-as-adapter build (pre-registered JOURNAL 2026-08-11 16:32).

1. Carve k=128 whitened top-subspace per fc-adapter tensor:
   W·L = UΣVᵀ  →  C = B·A  (B = U_kΣ_k, A = V_kᵀ·L⁻¹).
2. Deflated base: in-place overwrite of carved tensors in a COPY of
   the BF16 GGUF with bf16(W − C) (RNE truncation) — offsets/sizes
   unchanged, no writer round-trip.
3. Export the carve as a Q8 LoRA adapter (byte-equal to flagship).
Quantize step runs separately (llama-quantize, MIXED recipe).
"""
import shutil
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
GRAMDIR = REPO / "research/lowrank/data/gram27b-bf16"
DEF = HERE / "h27bf16-DEFLATED.gguf"
ADAPTER = HERE / "srr-carve-r128q8.gguf"
K = 128
QT = gguf.GGMLQuantizationType.Q8_0

KINDS_ATTN = ("attn_q", "attn_k", "attn_v", "attn_output")
KINDS_GDN = ("attn_qkv", "attn_gate", "ssm_out")


def rsvd(M, r, seed=0xBEA57):
    rng = np.random.default_rng(seed)
    k = min(r + 32, min(M.shape))
    Y = M @ rng.standard_normal((M.shape[1], k)).astype(np.float32)
    Y, _ = np.linalg.qr(Y)
    for _ in range(4):
        Y = M @ (M.T @ Y)
        Y, _ = np.linalg.qr(Y)
    Q, _ = np.linalg.qr(Y)
    Ub, s, Vt = np.linalg.svd(Q.T @ M, full_matrices=False)
    return (Q @ Ub)[:, :r], s[:r], Vt[:r]


def f32_to_bf16_bytes(a):
    u = a.astype(np.float32).view(np.uint32)
    rounded = u + 0x7FFF + ((u >> 16) & 1)        # round-to-nearest-even
    return (rounded >> 16).astype(np.uint16)


def main():
    t0 = time.time()
    if not DEF.exists():
        print(f"copying BF16 -> {DEF.name} ...", flush=True)
        shutil.copyfile(BF16, DEF)
    ref, arch = ex.index_2d(str(BF16))
    man = ex.load_grams(str(GRAMDIR))

    targets = []
    for name in ref:
        parts = name.split(".")
        if len(parts) < 4 or parts[0] != "blk" or int(parts[1]) >= 64:
            continue
        kind = parts[2]
        if kind in KINDS_ATTN or kind in KINDS_GDN:
            targets.append(name)
    print(f"{len(targets)} carve targets", flush=True)

    # tensor data offsets in the copy: re-read via GGUFReader on DEF
    rdef = gguf.GGUFReader(str(DEF), "r+")
    dmap = {t.name: t for t in rdef.tensors}

    w = gguf.GGUFWriter(str(ADAPTER), arch)
    w.add_type(gguf.GGUFType.ADAPTER)
    w.add_string(gguf.Keys.Adapter.TYPE, "lora")
    w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, float(K))  # uniform rank

    n_done = 0
    for name in sorted(targets):
        t = ref[name]
        W = dequantize(t.data, t.tensor_type).astype(np.float32)
        n_in = W.shape[1]
        wh = ex.gram_whitener(str(GRAMDIR), man, name, n_in)
        if wh is None or len(wh) != 1:
            print(f"SKIP {name}: no single-block gram", flush=True)
            continue
        L = wh[0][1].astype(np.float32)
        M = W @ L
        U, s, Vt = rsvd(M, K)
        B = (U * s[None, :]).astype(np.float64)               # (out, K)
        A = np.linalg.solve(L.T.astype(np.float64),
                            Vt.T.astype(np.float64)).T        # (K, n_in)
        C = (B @ A).astype(np.float32)
        Wd = W - C
        # in-place overwrite in the deflated copy (bf16, same bytes)
        dt = dmap[name]
        assert dt.tensor_type == t.tensor_type, name
        dt.data[:] = f32_to_bf16_bytes(Wd).view(dt.data.dtype).reshape(
            dt.data.shape)
        # balance + export carve as LoRA (correction ADDS C back)
        bn = np.linalg.norm(B, axis=0)
        an = np.linalg.norm(A, axis=1)
        eta = np.sqrt(np.maximum(bn, 1e-30) / np.maximum(an, 1e-30))
        Bq = (B / eta[None, :]).astype(np.float32)
        Aq = (A * eta[:, None]).astype(np.float32)
        w.add_tensor(name + ".lora_a", quantize(np.ascontiguousarray(Aq), QT),
                     raw_dtype=QT)
        w.add_tensor(name + ".lora_b", quantize(np.ascontiguousarray(Bq), QT),
                     raw_dtype=QT)
        cap = float((s.astype(np.float64) ** 2).sum()
                    / max((M.astype(np.float64) ** 2).sum(), 1e-30))
        n_done += 1
        print(f"{name} capW={cap:.3f} ({n_done}/{len(targets)})", flush=True)
        del W, M, C, Wd

    del rdef  # flush mmap writes
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    print(f"deflated file updated in place: {DEF}")
    print(f"adapter: {ADAPTER} ({ADAPTER.stat().st_size/1e6:.1f} MB)")
    print(f"total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
