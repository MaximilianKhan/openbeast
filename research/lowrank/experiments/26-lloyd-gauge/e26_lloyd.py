#!/usr/bin/env python3
"""E26-M4: Fisher-Lloyd fixed point on FROZEN Q2_K 4-bit sub-scales.

The servable Lloyd alternation, fusing E13 (codes) and the E17/E18 lane
(decoder refit) WITHOUT changing the decoder: the only refit allowed is
the affine component that folds into the existing per-superblock fp16
d/dmin — so every iterate remains a byte-compatible Q2_K file that stock
llama.cpp serves unmodified.

Alternation (both steps descend the SAME damped whitened metric
H = S + 0.01*mean(diag S)*I, S = E[xx^T] from the input Grams):
  codes-step:  E13's exact GPTQ/LDLQ column sweep on the current grid
               (dl, ml rebuilt from the refit d/dmin; 4-bit sc/m frozen).
  scales-step: with codes frozen, per-ROW generalized least squares for
               (d, dmin) of every superblock JOINTLY under H (superblocks
               couple through off-diagonal S within the row); solution
               cast to fp16 (the stored format). Guards: keep the old
               (d, dmin) where the solve is non-finite or d < 0 (the
               rounding sweep assumes dl >= 0).
Fixed point: codes unchanged between consecutive codes-steps (then the
scales-step is deterministic in the codes -> true fixed point), or
|dJ|/J < 1e-5, or --max-iters. Single pre-registered config, no sweeps.

Iteration 1's codes-step IS the published single-pass RR-input (same
code path, same grid) — the paired comparison vs RR isolates exactly
the extra Lloyd iterations.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "24-yaqa-lite"))
from e13b_yaqa import (_GRAM_SOURCE, gptq_hinv, q2k_pack_codes,  # noqa: E402
                       reround_tensor)

sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import REPO, deq, index_2d  # noqa: E402

sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader, GGUFWriter  # noqa: E402

QK_K = 256
DAMP = 0.01          # E13 convention; the metric both steps descend
RIDGE = 1e-8         # relative ridge on the per-row normal matrix
MAX_ITERS_DEFAULT = 8
J_RTOL = 1e-5


def q2k_split(raw, n_out, n_in):
    """raw -> (d, dmin) fp32 (n_out, nsb), (sc, m) fp32 (n_out, nsb, 16),
    and the raw block array."""
    blocks = raw.reshape(-1, 84)
    nsb = n_in // QK_K
    scales = blocks[:, :16]
    d = blocks[:, 80:82].copy().view(np.float16).astype(np.float32)
    dmin = blocks[:, 82:84].copy().view(np.float16).astype(np.float32)
    return (d.reshape(n_out, nsb), dmin.reshape(n_out, nsb),
            (scales & 0xF).astype(np.float32).reshape(n_out, nsb, 16),
            (scales >> 4).astype(np.float32).reshape(n_out, nsb, 16),
            blocks)


def expand_grid(d, dmin, sc, m, n_out, n_in):
    """(d,dmin,sc,m) -> dl, ml (n_out, n_in) fp32."""
    dl = np.repeat((d[..., None] * sc).reshape(n_out, -1), 16, axis=1)
    ml = np.repeat((dmin[..., None] * m).reshape(n_out, -1), 16, axis=1)
    return dl.reshape(n_out, n_in), ml.reshape(n_out, n_in)


def scales_step(W, q, sc, m, d_old, dmin_old, H):
    """per-row joint GLS for (d, dmin) of all superblocks under metric H.
    Returns (d_new, dmin_new) fp32 (pre-fp16), J_old, J_new, n_guard."""
    n_out, n_in = W.shape
    nsb = n_in // QK_K
    # per-element basis: model = d_k * a - dmin_k * b within superblock k
    a = (sc[..., None] * q.reshape(n_out, nsb, 16, 16)).reshape(
        n_out, nsb, QK_K)
    b = np.broadcast_to(m[..., None], (n_out, nsb, 16, 16)).reshape(
        n_out, nsb, QK_K)
    M = np.empty((n_out, nsb, QK_K, 2))
    M[..., 0] = a
    M[..., 1] = -b
    SW = W.astype(np.float64) @ H            # (n_out, n_in)
    wSw = np.einsum('ij,ij->i', W, SW)
    N = np.empty((n_out, 2 * nsb, 2 * nsb))
    r = np.empty((n_out, 2 * nsb))
    for l in range(nsb):
        cl = slice(l * QK_K, (l + 1) * QK_K)
        # X = S[:, cl] @ M_l  -> (n_out, n_in, 2)
        X = np.einsum('cd,idf->icf', H[:, cl], M[:, l])
        Xb = X.reshape(n_out, nsb, QK_K, 2)
        N[:, :, 2 * l:2 * l + 2] = np.einsum(
            'ikce,ikcf->ikef', M, Xb).reshape(n_out, 2 * nsb, 2)
        r[:, 2 * l:2 * l + 2] = np.einsum(
            'ice,ic->ie', M[:, l], SW[:, cl])
    lam = RIDGE * np.maximum(
        np.einsum('ikk->i', N) / (2 * nsb), 1e-30)[:, None]
    N[:, np.arange(2 * nsb), np.arange(2 * nsb)] += lam
    p_old = np.empty((n_out, 2 * nsb))
    p_old[:, 0::2] = d_old
    p_old[:, 1::2] = dmin_old
    try:
        p = np.linalg.solve(N, r[..., None])[..., 0]
    except np.linalg.LinAlgError:
        p = np.stack([np.linalg.lstsq(N[i], r[i], rcond=None)[0]
                      for i in range(n_out)])
    J = lambda pp: wSw - 2 * np.einsum('ik,ik->i', pp, r) + \
        np.einsum('ik,ikl,il->i', pp, N, pp)
    J_old = float(J(p_old).sum())
    d_new, dmin_new = p[:, 0::2].copy(), p[:, 1::2].copy()
    guard = ~np.isfinite(d_new) | ~np.isfinite(dmin_new) | (d_new < 0)
    d_new[guard] = d_old[guard]
    dmin_new[guard] = dmin_old[guard]
    # fp16 = the stored format; refit only counts if it survives the cast
    d_new = d_new.astype(np.float16).astype(np.float32)
    dmin_new = dmin_new.astype(np.float16).astype(np.float32)
    p_new = np.empty_like(p_old)
    p_new[:, 0::2] = d_new
    p_new[:, 1::2] = dmin_new
    J_new = float(J(p_new).sum())
    return d_new, dmin_new, J_old, J_new, int(guard.sum())


def pack_full(blocks, q, d, dmin):
    """codes + refit d/dmin -> raw Q2_K blocks (sc/m bytes untouched)."""
    out = q2k_pack_codes(blocks, q)
    out[:, 80:82] = d.astype(np.float16).reshape(-1, 1).view(np.uint8)
    out[:, 82:84] = dmin.astype(np.float16).reshape(-1, 1).view(np.uint8)
    return out


def lloyd_tensor(Wref, blocks_raw, n_out, n_in, S_damped, U, max_iters):
    """-> (q, d, dmin, info)."""
    d, dmin, sc, m, _ = q2k_split(blocks_raw, n_out, n_in)
    q_prev = None
    trace = []
    n_guard = 0
    it = 0
    for it in range(1, max_iters + 1):
        dl, ml = expand_grid(d, dmin, sc, m, n_out, n_in)
        q, _ = reround_tensor(Wref, dl, ml, U)
        if q_prev is not None and np.array_equal(q, q_prev):
            it -= 1          # this codes-step changed nothing
            break
        q_prev = q
        d, dmin, J_old, J_new, ng = scales_step(
            Wref.astype(np.float64), q, sc, m, d, dmin, S_damped)
        n_guard += ng
        trace.append((J_old, J_new))
        if len(trace) >= 2 and abs(trace[-2][1] - J_new) <= \
                J_RTOL * max(abs(J_new), 1e-30):
            break
    return q_prev, d, dmin, {"iters": it, "J_trace": trace,
                             "n_guard": n_guard}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("gram_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--state-dir", required=True,
                    help="per-tensor (codes,d,dmin) cache, fingerprinted")
    ap.add_argument("--max-iters", type=int, default=MAX_ITERS_DEFAULT)
    ap.add_argument("--max-tensors", type=int, default=0)
    ap.add_argument("--only", default=None,
                    help="driver-only: regex; compute only matching "
                         "tensors (parallel workers on disjoint sets)")
    ap.add_argument("--compute-only", action="store_true",
                    help="driver-only: fill the state cache, write no "
                         "GGUF (assembly run comes after)")
    args = ap.parse_args()

    man = json.loads((Path(args.gram_dir) / "grams.json").read_text())
    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    fp = {"quant": hashlib.sha256(
              Path(args.quant_gguf).read_bytes()).hexdigest()[:16],
          "gram_dir": str(Path(args.gram_dir).resolve()),
          "algo": "lloyd-v1-dmin-refit", "damp": DAMP, "ridge": RIDGE,
          "max_iters": args.max_iters, "j_rtol": J_RTOL}
    mfile = state / "meta.json"
    if mfile.exists():
        if json.loads(mfile.read_text()) != fp:
            sys.exit(f"[state-dir] fingerprint mismatch vs {mfile}")
    else:
        mfile.write_text(json.dumps(fp, indent=1))

    s_cache = {}

    def s_for(name, n_in):
        src = name
        for pat, fn in _GRAM_SOURCE:
            if re.search(pat, name):
                src = fn(name)
                break
        if src not in man or man[src]["n"] != n_in:
            return None
        if man[src]["blocks"] != 1:
            return None       # disclose: blocked grams unsupported here
        if src not in s_cache:
            g = np.fromfile(Path(args.gram_dir) /
                            (src.replace("/", "_") + ".gram.bin"),
                            dtype=np.float32)
            S = g.reshape(n_in, n_in).astype(np.float64) \
                / man[src]["count"]
            S = 0.5 * (S + S.T)
            Sd = S.copy()
            Sd[np.diag_indices(n_in)] += DAMP * float(np.mean(np.diag(S)))
            s_cache[src] = (Sd, gptq_hinv(S).astype(np.float32))
        return s_cache[src]

    ref, _ = index_2d(args.ref_gguf)
    rdr = GGUFReader(args.quant_gguf)
    w = None
    if not args.compute_only:
        arch = rdr.get_field("general.architecture").contents()
        w = GGUFWriter(args.out, arch)
        for key, f in rdr.fields.items():
            if key.startswith("GGUF.") or key == "general.architecture":
                continue
            sub = f.types[-1] if f.types[0] == gguf.GGUFValueType.ARRAY \
                else None
            w.add_key_value(key, f.contents(), f.types[0], sub_type=sub)

    n_re = n_new = 0
    # workers write disjoint stats files (no shared-json race); every
    # run merges all of them on load
    stats = {}
    for sf in sorted(state.glob("stats*.json")):
        stats.update(json.loads(sf.read_text()))
    tag = ("-w" + re.sub(r"[^0-9A-Za-z]+", "", args.only)[:16]
           if args.only else "")
    sfile = state / f"stats{tag}.json"
    my_stats = json.loads(sfile.read_text()) if sfile.exists() else {}
    t0 = time.time()
    for t in rdr.tensors:
        is_q2k = t.tensor_type == gguf.GGMLQuantizationType.Q2_K
        shape = [int(x) for x in t.shape if int(x) > 1]
        if is_q2k and len(shape) == 2 and t.name in ref:
            n_out, n_in = shape[1], shape[0]
            SU = s_for(t.name, n_in)
            if SU is None:
                if w:
                    w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
                continue
            cfile = state / (t.name.replace("/", "_") + ".npz")
            raw = np.asarray(t.data).reshape(-1)
            if cfile.exists():
                z = np.load(cfile)
                q, d, dmin = z["q"], z["d"], z["dmin"]
            elif args.only and not re.search(args.only, t.name):
                if w:
                    sys.exit(f"assembly run: {t.name} not yet cached")
                continue
            else:
                if args.max_tensors and n_new >= args.max_tensors:
                    print(f"[chunk] limit reached ({n_new} new)",
                          flush=True)
                    return
                Sd, U = SU
                Wref = deq(ref[t.name])
                tt = time.time()
                q, d, dmin, info = lloyd_tensor(
                    Wref, raw, n_out, n_in, Sd, U, args.max_iters)
                n_new += 1
                tmp = cfile.with_suffix(".tmp.npz")
                np.savez(tmp, q=q, d=d, dmin=dmin)
                tmp.rename(cfile)
                stats[t.name] = my_stats[t.name] = info
                sfile.write_text(json.dumps(my_stats, indent=1))
                j0, j1 = info["J_trace"][0][0], info["J_trace"][-1][1]
                print(f"[lloyd] {t.name} ({n_out}x{n_in}) "
                      f"iters={info['iters']} J {j0:.4g}->{j1:.4g} "
                      f"({100*(1-j1/j0):.2f}% off) guard={info['n_guard']} "
                      f"{time.time()-tt:.1f}s", flush=True)
            if w:
                _, _, _, _, blocks = q2k_split(raw, n_out, n_in)
                nb2 = pack_full(blocks, q.reshape(-1), d, dmin)
                w.add_tensor(t.name,
                             nb2.reshape(np.asarray(t.data).shape),
                             raw_dtype=gguf.GGMLQuantizationType.Q2_K)
            n_re += 1
        elif w:
            w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
    if w:
        w.write_header_to_file()
        w.write_kv_data_to_file()
        w.write_tensors_to_file(progress=False)
        w.close()
    iters = [v["iters"] for v in stats.values()]
    print(f"{'wrote ' + args.out if w else '[compute-only]'}: "
          f"{n_re} tensors Lloyd-refit, "
          f"iters min/med/max = {min(iters)}/"
          f"{sorted(iters)[len(iters)//2]}/{max(iters)}, "
          f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
