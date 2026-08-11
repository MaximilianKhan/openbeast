#!/usr/bin/env python3
"""E26 validation: (1) pack_full byte-roundtrip on a real tensor;
(2) scales_step == brute-force whitened LS (pre-guard), and descends J;
(3) expand_grid == e13b's q2k_decode expansion on real bytes."""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "24-yaqa-lite"))
from e13b_yaqa import q2k_decode  # noqa: E402
from e26_lloyd import (QK_K, expand_grid, pack_full, q2k_split,  # noqa: E402
                       scales_step)

sys.path.insert(0, str(HERE.parent / "04-served-v0"))
from extract_adapter import REPO  # noqa: E402
sys.path.insert(0, str(REPO / "llama.cpp" / "gguf-py"))
import gguf  # noqa: E402
from gguf import GGUFReader  # noqa: E402

Q2K = HERE.parent / "22-qwen35-08b" / "q35-08b-Q2_K.gguf"

# --- real-bytes tests -------------------------------------------------
rdr = GGUFReader(str(Q2K))
t = next(t for t in rdr.tensors
         if t.name == "blk.0.ffn_up.weight")
shape = [int(x) for x in t.shape if int(x) > 1]
n_out, n_in = shape[1], shape[0]
raw = np.asarray(t.data).reshape(-1)
d, dmin, sc, m, blocks = q2k_split(raw, n_out, n_in)

# (3) grid expansion identical to the verified E13 decoder
dl_ref, ml_ref, _ = q2k_decode(raw, n_out, n_in)
dl, ml = expand_grid(d, dmin, sc, m, n_out, n_in)
assert np.array_equal(dl, dl_ref) and np.array_equal(ml, ml_ref), \
    "expand_grid mismatch"
print("PASS expand_grid == q2k_decode on real bytes")

# (1) byte roundtrip: original codes + original d/dmin -> same bytes
safe = np.where(dl > 0, dl, 1.0)
Wdeq = gguf.dequantize(t.data, t.tensor_type).reshape(n_out, n_in)
q = np.clip(np.rint((Wdeq + ml) / safe), 0, 3).astype(np.uint8)
out = pack_full(blocks, q.reshape(-1), d, dmin)
assert np.array_equal(out, blocks), "pack_full roundtrip mismatch"
print("PASS pack_full byte-roundtrip on real tensor")

# --- synthetic scales_step tests --------------------------------------
rng = np.random.default_rng(0)
fails = 0
for trial in range(10):
    m_rows, n_cols = 8, 4 * QK_K          # 4 superblocks
    nsb = n_cols // QK_K
    A = rng.standard_normal((n_cols, 2 * n_cols)) / np.sqrt(2 * n_cols)
    H = A @ A.T + 0.05 * np.eye(n_cols)
    W = rng.standard_normal((m_rows, n_cols))
    sc = rng.integers(1, 16, (m_rows, nsb, 16)).astype(np.float32)
    mm = rng.integers(0, 16, (m_rows, nsb, 16)).astype(np.float32)
    q = rng.integers(0, 4, (m_rows, n_cols)).astype(np.uint8)
    d0 = np.abs(rng.standard_normal((m_rows, nsb))).astype(np.float32) * .1
    dm0 = np.abs(rng.standard_normal((m_rows, nsb))).astype(np.float32) * .1
    d1, dm1, J_old, J_new, ng = scales_step(W, q, sc, mm, d0, dm0, H)
    # brute force: per row, whitened lstsq over explicit M
    L = np.linalg.cholesky(H)
    a = (sc[..., None] * q.reshape(m_rows, nsb, 16, 16)).reshape(
        m_rows, nsb, QK_K)
    b = np.broadcast_to(mm[..., None], (m_rows, nsb, 16, 16)).reshape(
        m_rows, nsb, QK_K)
    for i in range(m_rows):
        M = np.zeros((n_cols, 2 * nsb))
        for k in range(nsb):
            M[k * QK_K:(k + 1) * QK_K, 2 * k] = a[i, k]
            M[k * QK_K:(k + 1) * QK_K, 2 * k + 1] = -b[i, k]
        p_bf, *_ = np.linalg.lstsq(L.T @ M, L.T @ W[i], rcond=None)
        got = np.empty(2 * nsb)
        got[0::2], got[1::2] = d1[i], dm1[i]
        want = p_bf.copy()
        bad = want[0::2] < 0                 # guard branch
        if bad.any():
            continue                         # brute force lacks the guard
        want[0::2] = want[0::2].astype(np.float16)
        want[1::2] = want[1::2].astype(np.float16)
        if not np.allclose(got, want, rtol=2e-3, atol=1e-6):
            fails += 1
    assert J_new <= J_old * (1 + 1e-6) + 1e-9, \
        f"J increased: {J_old} -> {J_new}"
assert fails == 0, f"{fails} rows mismatch brute force"
print("PASS scales_step == whitened lstsq (10 trials) and J descends")
