#!/usr/bin/env python3
"""E23: extract whitened low-rank corrections for MoE expert stacks and
export them as 3D LoRA GGUFs.

Per stack (3D tensor W[e] of shape (n_expert, n_out, n_in)):
  R_e  = W_ref_e - dequant(base_e)          per-expert residual
  L    = chol(G/count + damp)               POOLED stack Gram (capture E23);
                                            gate maps to up's Gram
  Rd_e = R_e @ L                            right-whitened residual

Variant A (per-expert):  B_e = top-r left singular vecs of Rd_e,
                         A_e = B_e^T R_e    (L cancels: projection form)
Variant B (shared basis): B = top-r eigvecs of sum_e Rd_e Rd_e^T
                         (= column basis of the column-concatenated pooled
                         whitened residual), A_e = B^T R_e.

Export: <name>.lora_a ne=(n_in, r, n_expert), <name>.lora_b ne=(r, n_out,
n_expert) for A; shared-basis lora_b is 2D ne=(r, n_out). Q8_0 factors.
llama.cpp's build_lora_mm_id serves the 3D pair natively; the 2D-B pair
needs the one-branch graph extension (see README).
"""
import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader  # noqa: E402
from gguf.quants import dequantize, quantize as gquant  # noqa: E402

Q8 = gguf.GGMLQuantizationType.Q8_0


def index_exps(path):
    out = {}
    r = GGUFReader(path)
    arch = r.get_field("general.architecture").contents()
    for t in r.tensors:
        if t.name.endswith("_exps.weight"):
            out[t.name] = t
    return out, arch


def deq3(t):
    """-> (n_expert, n_out, n_in) float32."""
    shape = [int(d) for d in t.shape]          # ne order (n_in, n_out, n_e)
    w = dequantize(t.data, t.tensor_type).astype(np.float32)
    return w.reshape(shape[::-1])


def whitener(gram_dir, man, name, n_in, damp=0.01):
    src = name.replace("ffn_gate_exps.", "ffn_up_exps.")
    m = man[src]
    assert m["n"] == n_in and m["blocks"] == 1, (name, m)
    g = np.fromfile(Path(gram_dir) / (src + ".gram.bin"), dtype=np.float32)
    S = g.reshape(n_in, n_in).astype(np.float64) / m["count"]
    S = 0.5 * (S + S.T)
    S[np.diag_indices(n_in)] += damp * float(np.mean(np.diag(S)))
    return np.linalg.cholesky(S).astype(np.float32)


def process_stack(job):
    name, ref_path, base_path, gram_dir, rank, scratch = job
    t0 = time.time()
    man = json.loads((Path(gram_dir) / "grams.json").read_text())
    ref_t = index_exps(ref_path)[0][name]
    base_t = index_exps(base_path)[0][name]
    W = deq3(ref_t)
    R = W - deq3(base_t)
    del W
    n_e, n_out, n_in = R.shape
    L = whitener(gram_dir, man, name, n_in)
    Rd = R @ L                                   # (n_e, n_out, n_in)
    tot_e = (Rd.astype(np.float64) ** 2).sum(axis=(1, 2))    # per-expert

    if n_out <= n_in:
        # eigh on the (n_out, n_out) side, batched over experts
        C = (Rd @ Rd.transpose(0, 2, 1)).astype(np.float64)
        C = 0.5 * (C + C.transpose(0, 2, 1))
        ev, evec = np.linalg.eigh(C)             # ascending
        Bp = evec[:, :, -rank:].astype(np.float32)           # (n_e,n_out,r)
        cap_pe = ev[:, -rank:].sum(axis=1)
        G = C.sum(axis=0)
        evs, evecs = np.linalg.eigh(G)
        Bs = evecs[:, -rank:].astype(np.float32)             # (n_out, r)
    else:
        # down: eigh on the small (n_in, n_in) side, lift to output space
        N = (Rd.transpose(0, 2, 1) @ Rd).astype(np.float64)
        N = 0.5 * (N + N.transpose(0, 2, 1))
        ev, evec = np.linalg.eigh(N)
        V = evec[:, :, -rank:]                   # (n_e, n_in, r)
        s = np.sqrt(np.maximum(ev[:, -rank:], 1e-30))
        Bp = (Rd.astype(np.float64) @ (V / s[:, None, :])).astype(np.float32)
        cap_pe = ev[:, -rank:].sum(axis=1)
        M = Rd.transpose(1, 0, 2).reshape(n_out, -1)
        G = (M @ M.T).astype(np.float64)
        G = 0.5 * (G + G.T)
        evs, evecs = np.linalg.eigh(G)
        Bs = evecs[:, -rank:].astype(np.float32)

    Ap = Bp.transpose(0, 2, 1) @ R               # (n_e, r, n_in)
    As = np.einsum("or,eoi->eri", Bs, R).astype(np.float32)
    cap_sh = ((Bs.T @ Rd) ** 2).astype(np.float64).sum(axis=(1, 2))

    out = {
        "pe_a": gquant(np.ascontiguousarray(Ap), Q8),
        "pe_b": gquant(np.ascontiguousarray(Bp), Q8),
        "sh_a": gquant(np.ascontiguousarray(As), Q8),
        "sh_b": gquant(np.ascontiguousarray(Bs), Q8),
    }
    stats = {
        "tot": float(tot_e.sum()),
        "cap_pe": float(cap_pe.sum()),
        "cap_sh": float(cap_sh.sum()),
        "shapes": [list(Ap.shape), list(Bp.shape), list(Bs.shape)],
    }
    np.savez(Path(scratch) / (name + ".npz"), **out)
    (Path(scratch) / (name + ".json")).write_text(json.dumps(stats))
    return name, stats, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("base_gguf")
    ap.add_argument("gram_dir")
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--out-pe", required=True)
    ap.add_argument("--out-sh", required=True)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    Path(args.scratch).mkdir(parents=True, exist_ok=True)
    ref, arch = index_exps(args.ref_gguf)
    base, _ = index_exps(args.base_gguf)
    names = sorted(set(ref) & set(base),
                   key=lambda n: (int(n.split(".")[1]), n))
    print(f"{len(names)} expert stacks, arch={arch}", flush=True)

    jobs = [(n, args.ref_gguf, args.base_gguf, args.gram_dir, args.rank,
             args.scratch) for n in names
            if not (Path(args.scratch) / (n + ".json")).exists()]
    t0 = time.time()
    if jobs:
        with mp.get_context("spawn").Pool(args.workers) as pool:
            for name, st, dt in pool.imap_unordered(process_stack, jobs):
                print(f"{name:36s} cap_pe={st['cap_pe']/st['tot']:.3f} "
                      f"cap_sh={st['cap_sh']/st['tot']:.3f} {dt:.0f}s",
                      flush=True)
    print(f"extraction {time.time()-t0:.0f}s; assembling ggufs", flush=True)

    caps = {"pe": [0.0, 0.0], "sh": [0.0, 0.0]}
    writers = {}
    for tag, out in (("pe", args.out_pe), ("sh", args.out_sh)):
        w = gguf.GGUFWriter(out, arch)
        w.add_type(gguf.GGUFType.ADAPTER)
        w.add_string(gguf.Keys.Adapter.TYPE, "lora")
        w.add_float32(gguf.Keys.Adapter.LORA_ALPHA, float(args.rank))
        writers[tag] = w
    for n in names:
        z = np.load(Path(args.scratch) / (n + ".npz"))
        st = json.loads((Path(args.scratch) / (n + ".json")).read_text())
        writers["pe"].add_tensor(n + ".lora_a", z["pe_a"], raw_dtype=Q8)
        writers["pe"].add_tensor(n + ".lora_b", z["pe_b"], raw_dtype=Q8)
        writers["sh"].add_tensor(n + ".lora_a", z["sh_a"], raw_dtype=Q8)
        writers["sh"].add_tensor(n + ".lora_b", z["sh_b"], raw_dtype=Q8)
        for tag in ("pe", "sh"):
            caps[tag][0] += st[f"cap_{tag}"]
            caps[tag][1] += st["tot"]
    for tag, out in (("pe", args.out_pe), ("sh", args.out_sh)):
        w = writers[tag]
        w.write_header_to_file()
        w.write_kv_data_to_file()
        w.write_tensors_to_file(progress=False)
        w.close()
        print(f"{out}: {Path(out).stat().st_size/1e6:.1f} MB, "
              f"whitened-energy captured {caps[tag][0]/caps[tag][1]:.4f}")


if __name__ == "__main__":
    main()
