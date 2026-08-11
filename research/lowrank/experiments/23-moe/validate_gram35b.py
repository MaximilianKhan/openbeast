#!/usr/bin/env python3
"""E23 gate: validate MoE (MUL_MAT_ID) Gram capture on Qwen3.6-35B-A3B.

Checks, per gram entry:
  1. symmetry:   max|G - G^T| / max|G|
  2. PSD:        min eigenvalue of S = sym(G)/count  (>= -tol * max_eig)
  3. slogdet:    finite logdet of S + 1e-3 * mean(diag(S)) * I  (sign = +1)
  4. diag:       diag(G) == imatrix in_sum2 pooled across experts
                 (MoE: sum over expert rows; dense: the single row)
  5. counts:     gram count == sum(imatrix counts)
                 (MoE: routed (token, slot) pairs = n_tokens * n_expert_used;
                  dense: n_tokens)
Bonus: gate_exps in_sum2 must equal up_exps in_sum2 (same input, same routing).

Exit nonzero on any gate failure.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/max/Documents/openbeast/llama.cpp/gguf-py")
from gguf import GGUFReader  # noqa: E402

GRAM_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else
                "/home/max/Documents/openbeast/research/lowrank/data/gram35b")
IMATRIX = GRAM_DIR / "diag.imatrix.gguf"

N_CHUNKS, N_CTX, N_EXPERT_USED = 16, 512, 8
TOKENS = N_CHUNKS * N_CTX
SYM_TOL = 1e-5      # relative asymmetry from threaded accumulation order
EIG_TOL = 1e-6      # relative negative-eigenvalue tolerance
DIAG_TOL = 2e-4     # relative diag mismatch (f32 sums, different add order)


def load_imatrix(path):
    sums, counts = {}, {}
    for t in GGUFReader(path).tensors:
        data = np.asarray(t.data, dtype=np.float64).ravel()
        if t.name.endswith(".in_sum2"):
            sums[t.name[: -len(".in_sum2")]] = data
        elif t.name.endswith(".counts"):
            counts[t.name[: -len(".counts")]] = data
    return sums, counts


def main():
    man = json.loads((GRAM_DIR / "grams.json").read_text())
    sums, counts = load_imatrix(IMATRIX)

    n_fail = 0
    worst = {"sym": 0.0, "eig": 0.0, "diag": 0.0}
    kinds = {}

    for name in sorted(man):
        m = man[name]
        n, blocks, count = m["n"], m["blocks"], m["count"]
        g = np.fromfile(GRAM_DIR / (name.replace("/", "_") + ".gram.bin"),
                        dtype=np.float32)
        bs = n // blocks
        ok = True
        if g.size != blocks * bs * bs:
            print(f"FAIL {name}: file size {g.size} != {blocks}*{bs}^2")
            n_fail += 1
            continue

        is_moe = "_exps." in name
        # pooled imatrix diag + count for this gram's source tensor
        s = sums.get(name)
        c = counts.get(name)
        if s is None or c is None:
            print(f"FAIL {name}: no imatrix entry")
            n_fail += 1
            continue
        if is_moe:
            diag_ref = s.reshape(-1, n).sum(axis=0)
            count_ref = int(c.sum())
            count_exp = TOKENS * N_EXPERT_USED
        else:
            diag_ref = s
            count_ref = int(c.sum())  # dense: single count
            count_exp = TOKENS

        diag_g = np.empty(n, dtype=np.float64)
        min_eig_rel = np.inf
        for b in range(blocks):
            G = g[b * bs * bs:(b + 1) * bs * bs].reshape(bs, bs).astype(np.float64)
            scale = np.abs(G).max() or 1.0
            sym = np.abs(G - G.T).max() / scale
            worst["sym"] = max(worst["sym"], sym)
            if sym > SYM_TOL:
                print(f"FAIL {name}[{b}]: asymmetry {sym:.2e}")
                ok = False
            S = 0.5 * (G + G.T) / max(count, 1)
            ev = np.linalg.eigvalsh(S)
            rel = ev[0] / max(ev[-1], 1e-300)
            min_eig_rel = min(min_eig_rel, rel)
            worst["eig"] = min(worst["eig"], rel) if worst["eig"] else rel
            if rel < -EIG_TOL:
                print(f"FAIL {name}[{b}]: min eig rel {rel:.2e}")
                ok = False
            D = S.copy()
            D[np.diag_indices(bs)] += 1e-3 * float(np.mean(np.diag(S)))
            sign, logdet = np.linalg.slogdet(D)
            if sign <= 0 or not np.isfinite(logdet):
                print(f"FAIL {name}[{b}]: slogdet sign={sign} logdet={logdet}")
                ok = False
            diag_g[b * bs:(b + 1) * bs] = np.diag(G)

        dref = np.where(diag_ref == 0, 1.0, diag_ref)
        diag_rel = np.abs(diag_g - diag_ref).max() / np.abs(dref).max() \
            if diag_ref.any() else np.abs(diag_g).max()
        diag_rel = float(np.abs((diag_g - diag_ref) / np.abs(dref)).max())
        worst["diag"] = max(worst["diag"], diag_rel)
        if diag_rel > DIAG_TOL:
            print(f"FAIL {name}: diag rel err {diag_rel:.2e}")
            ok = False
        if count != count_ref:
            print(f"FAIL {name}: count {count} != imatrix sum {count_ref}")
            ok = False
        if count != count_exp:
            print(f"FAIL {name}: count {count} != expected {count_exp}")
            ok = False

        kind = name.split(".", 2)[-1]
        k = kinds.setdefault(kind, {"n": n, "count": count, "layers": 0,
                                    "sym": 0.0, "eig": 0.0, "diag": 0.0,
                                    "logdet": []})
        k["layers"] += 1
        k["sym"] = max(k["sym"], sym)
        k["eig"] = min(k["eig"], min_eig_rel)
        k["diag"] = max(k["diag"], diag_rel)
        k["logdet"].append(logdet)
        if not ok:
            n_fail += 1

    # bonus: gate_exps imatrix must equal up_exps (same input, same routing)
    gate_up_max = 0.0
    for name in sums:
        if "ffn_gate_exps" in name:
            up = name.replace("ffn_gate_exps", "ffn_up_exps")
            if up in sums:
                d = np.abs(sums[name] - sums[up]).max()
                sc = np.abs(sums[up]).max() or 1.0
                gate_up_max = max(gate_up_max, d / sc)
    if gate_up_max > 1e-12:
        print(f"FAIL: gate_exps vs up_exps in_sum2 rel diff {gate_up_max:.2e}")
        n_fail += 1

    print(f"\nentries: {len(man)}  kinds:")
    for kind, k in sorted(kinds.items()):
        ld = np.array(k["logdet"])
        print(f"  {kind:24s} n={k['n']:5d} layers={k['layers']:3d} "
              f"count={k['count']:6d} sym={k['sym']:.1e} "
              f"eig_rel={k['eig']:+.1e} diag={k['diag']:.1e} "
              f"logdet=[{ld.min():.1f},{ld.max():.1f}]")
    print(f"worst: sym={worst['sym']:.2e} eig_rel={worst['eig']:+.2e} "
          f"diag={worst['diag']:.2e} gate_up={gate_up_max:.2e}")
    print("RESULT:", "FAIL" if n_fail else "PASS", f"({n_fail} failures)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
