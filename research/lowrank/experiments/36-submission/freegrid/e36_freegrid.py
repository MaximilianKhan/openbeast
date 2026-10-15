#!/usr/bin/env python3
"""E36-E: free-grid GPTQ baseline for the frozen-grid re-rounder (E13).

Same format, same bytes, same tensor coverage and same Gram/damping as
13-rerounder/e13_reround.py. The difference is that the Q2_K grid is
NOT frozen: the input columns are swept in order and, on entering each
256-column superblock, that superblock's grid (fp16 d/dmin and the
4-bit sub-block scales/mins) is refit for every row from the CURRENT
error-updated weights by llama.cpp's own Q2_K quantizer
(ggml_quantize_chunk via ctypes, with the imatrix weights for those 256
columns, normalised exactly as llama-quantize does). Inside the
superblock the 2-bit codes are then chosen column by column with E13's
GPTQ error feedback (decode with the new grid, propagate the whitened
error to every not-yet-quantized column, including later superblocks).

CPU only. Never touches a GPU.
"""
import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "12")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import argparse  # noqa: E402
import ctypes  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
EXP = HERE.parents[1]                      # research/lowrank/experiments
sys.path.insert(0, str(EXP / "13-rerounder"))
from e13_reround import (_GRAM_SOURCE, gptq_hinv, q2k_decode,  # noqa: E402
                         reround_tensor)
from extract_adapter import REPO, deq, index_2d  # noqa: E402,F401
import gguf  # noqa: E402  (path set up by extract_adapter)
from gguf import GGUFReader, GGUFWriter  # noqa: E402

QK_K = 256
Q2K_BYTES = 84
GGML_TYPE_Q2_K = int(gguf.GGMLQuantizationType.Q2_K)   # 10, == ggml.h
NTHREADS = int(os.environ.get("E36_THREADS", "12"))

# ---------------------------------------------------------------- ggml
_LIB = None


def lib():
    global _LIB
    if _LIB is None:
        so = os.environ.get("E36_GGML_BASE",
                            str(REPO / "llama.cpp" / "build" / "bin" /
                                "libggml-base.so"))
        L = ctypes.CDLL(so)
        L.ggml_quantize_chunk.restype = ctypes.c_size_t
        L.ggml_quantize_chunk.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p]
        L.ggml_quantize_requires_imatrix.restype = ctypes.c_bool
        L.ggml_quantize_requires_imatrix.argtypes = [ctypes.c_int]
        _LIB = L
    return _LIB


_POOL = None


def pool():
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=NTHREADS)
    return _POOL


def quantize_q2k(X, imat):
    """X (m, k) float32, k % 256 == 0; imat (k,) float32 or None.
    -> (m * k/256, 84) uint8, exactly what ggml_quantize_chunk writes.
    Rows are split across threads (ctypes releases the GIL); Q2_K's
    quantizer is row-independent, so the split does not change bytes."""
    X = np.ascontiguousarray(X, dtype=np.float32)
    m, k = X.shape
    assert k % QK_K == 0
    nbr = k // QK_K
    out = np.empty((m, nbr * Q2K_BYTES), dtype=np.uint8)
    im = None if imat is None else np.ascontiguousarray(imat,
                                                        dtype=np.float32)
    L = lib()
    imp = None if im is None else im.ctypes.data

    def run(r0, r1):
        L.ggml_quantize_chunk(GGML_TYPE_Q2_K, X[r0:r1].ctypes.data,
                              out[r0:r1].ctypes.data, 0, r1 - r0, k, imp)

    step = max(64, -(-m // NTHREADS))
    futs = [pool().submit(run, r0, min(m, r0 + step))
            for r0 in range(0, m, step)]
    for f in futs:
        f.result()
    return out.reshape(m * nbr, Q2K_BYTES)


# ------------------------------------------------------------- imatrix
def load_imatrix_llq(path):
    """Mirror tools/quantize/quantize.cpp load_imatrix (GGUF format):
    weights = in_sum2 / counts (float32 division, per expert slice;
    count 0 -> 1.0). Names are used as-is (remap_imatrix is identity
    unless layers were pruned)."""
    sums, counts = {}, {}
    for t in GGUFReader(path).tensors:
        if t.name.endswith(".in_sum2"):
            sums[t.name[:-8]] = np.asarray(t.data, np.float32).ravel()
        elif t.name.endswith(".counts"):
            counts[t.name[:-7]] = np.asarray(t.data, np.float32).ravel()
    out = {}
    for name, s in sums.items():
        c = counts[name].astype(np.int64).astype(np.float32)
        ne0 = s.size // c.size
        e = np.empty_like(s)
        for j in range(c.size):
            sl = slice(j * ne0, (j + 1) * ne0)
            e[sl] = s[sl] / c[j] if c[j] > 0 else np.float32(1.0)
        out[name] = e
    return out


# --------------------------------------------------------------- Q2_K
def q2k_unpack_codes(blocks):
    """(nb, 84) -> (nb, 256) codes in element order (inverse of
    e13.q2k_pack_codes)."""
    qs = blocks[:, 16:80]
    e = np.arange(256)
    g, r = e // 128, e % 128
    s, b = r // 32, r % 32
    return (qs[:, g * 32 + b] >> (2 * s).astype(np.uint8)) & 3


def q2k_pack(blocks, q):
    """blocks (nb,84), q (nb,256) -> copy with qs replaced."""
    e = np.arange(256)
    g, r = e // 128, e % 128
    s, b = r // 32, r % 32
    qs = np.zeros((blocks.shape[0], 64), dtype=np.uint8)
    q = q.astype(np.uint8)
    for i in range(256):
        qs[:, g[i] * 32 + b[i]] |= (q[:, i] & 3) << np.uint8(2 * s[i])
    out = blocks.copy()
    out[:, 16:80] = qs
    return out


def sb_grid(blocks):
    """(m, 84) superblocks -> dl, ml (m, 256) float32 (same arithmetic as
    e13.q2k_decode and ggml's requantize step)."""
    sc = blocks[:, :16]
    d = blocks[:, 80:82].copy().view(np.float16).astype(np.float32)
    dmin = blocks[:, 82:84].copy().view(np.float16).astype(np.float32)
    dl = d * (sc & 0xF).astype(np.float32)
    ml = dmin * (sc >> 4).astype(np.float32)
    return np.repeat(dl, 16, axis=1), np.repeat(ml, 16, axis=1)


def freegrid_block(Wref, imat, Hu, feedback=True, blk=128):
    """One Gram block. Wref (m, n) f32, n % 256 == 0, imat (n,), Hu upper
    (n, n) with inv(H) = HuᵀHu. -> blocks (m, n/256, 84) uint8, Wq."""
    W = Wref.astype(np.float32).copy()
    m, n = W.shape
    assert n % QK_K == 0 and QK_K % blk == 0
    nsb = n // QK_K
    out = np.empty((m, nsb, Q2K_BYTES), dtype=np.uint8)
    qcodes = np.empty((m, n), dtype=np.uint8)
    Wq = np.empty((m, n), dtype=np.float32)
    dl = ml = L0 = None
    for b0 in range(0, n, blk):
        b1 = min(b0 + blk, n)
        Err = np.zeros((m, b1 - b0), dtype=np.float32)
        for j in range(b0, b1):
            if j % QK_K == 0:
                # all feedback from earlier columns has landed in W here
                # (lazy-batch tails are applied at every 128 boundary)
                sb = j // QK_K
                bl = quantize_q2k(W[:, j:j + QK_K], imat[j:j + QK_K])
                out[:, sb] = bl
                dl, ml = sb_grid(bl)
                L0 = q2k_unpack_codes(bl)
            c = j % QK_K
            dlj, mlj = dl[:, c], ml[:, c]
            zero = dlj == 0
            safe = np.where(zero, np.float32(1.0), dlj)
            q = np.clip(np.rint((W[:, j] + mlj) / safe), 0, 3)
            # d == 0: every code decodes to -m; keep ggml's code so the
            # bytes match llama-quantize (value is identical either way)
            q = np.where(zero, L0[:, c], q).astype(np.uint8)
            wq = dlj * q.astype(np.float32) - mlj
            qcodes[:, j] = q
            Wq[:, j] = wq
            if not feedback:
                continue
            djj = Hu[j, j]
            if djj == 0:
                continue
            err = (W[:, j] - wq) / djj
            Err[:, j - b0] = err
            if j + 1 < b1:
                W[:, j + 1:b1] -= err[:, None] * Hu[j, j + 1:b1][None, :]
        if feedback and b1 < n:
            W[:, b1:] -= Err @ Hu[b0:b1, b1:]
    q2 = qcodes.reshape(m, nsb, QK_K).reshape(m * nsb, QK_K)
    out = q2k_pack(out.reshape(m * nsb, Q2K_BYTES), q2)
    return out.reshape(m, nsb, Q2K_BYTES), Wq


def freegrid_tensor(Wref, imat, Hu_blocks, feedback=True):
    """-> raw Q2_K blocks (m * n/256, 84) in GGUF order, and Wq."""
    m, n = Wref.shape
    out = np.empty((m, n // QK_K, Q2K_BYTES), dtype=np.uint8)
    Wq = np.empty((m, n), dtype=np.float32)
    for off, U in Hu_blocks:
        bs = U.shape[0]
        assert off % QK_K == 0 and bs % QK_K == 0, "Gram block not 256-aligned"
        ob, wq = freegrid_block(Wref[:, off:off + bs], imat[off:off + bs],
                                U, feedback=feedback)
        out[:, off // QK_K:(off + bs) // QK_K] = ob
        Wq[:, off:off + bs] = wq
    return out.reshape(-1, Q2K_BYTES), Wq


# ---------------------------------------------------------------- Gram
class Grams:
    """E13's Gram handling (same source mapping, normalisation, blocks,
    damping), with the last factorisation cached (q/k/v and gate/up
    share an input, and tensors arrive grouped by layer)."""

    def __init__(self, gram_dir, damp=0.01):
        self.dir = Path(gram_dir)
        self.man = json.loads((self.dir / "grams.json").read_text())
        self.damp = damp
        self._key = None
        self._S = None
        self._Hu = None

    def source(self, name):
        for pat, fn in _GRAM_SOURCE:
            if re.search(pat, name):
                return fn(name)
        return name

    def S_blocks(self, name, n_in):
        src = self.source(name)
        if src not in self.man or self.man[src]["n"] != n_in:
            return None
        if self._key != src:
            g = np.fromfile(self.dir / (src.replace("/", "_") + ".gram.bin"),
                            dtype=np.float32)
            nb = self.man[src]["blocks"]
            bs = n_in // nb
            self._S = [(b * bs, g[b * bs * bs:(b + 1) * bs * bs]
                        .reshape(bs, bs).astype(np.float64)
                        / self.man[src]["count"]) for b in range(nb)]
            self._Hu = None
            self._key = src
        return self._S

    def Hu_blocks(self, name, n_in):
        S = self.S_blocks(name, n_in)
        if S is None:
            return None
        if self._Hu is None:
            self._Hu = [(off, gptq_hinv(s, self.damp).astype(np.float32))
                        for off, s in S]
        return self._Hu


def whitened_err(Wref, Wq, S_blocks):
    """tr(E S Eᵀ), E = Wref - Wq, summed over Gram blocks."""
    tot = 0.0
    for off, S in S_blocks:
        bs = S.shape[0]
        E = (Wref[:, off:off + bs] - Wq[:, off:off + bs]).astype(np.float64)
        tot += float(np.sum((E @ S) * E))
    return tot


def frozen_wq(raw, n_out, n_in):
    """dequantized base Q2_K tensor (n_out, n_in), via e13's decoder."""
    dl, ml, blocks = q2k_decode(raw, n_out, n_in)
    q = q2k_unpack_codes(blocks).reshape(n_out, n_in).astype(np.float32)
    return dl * q - ml


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref_gguf")
    ap.add_argument("quant_gguf")
    ap.add_argument("gram_dir")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--imatrix", required=True,
                    help="the imatrix GGUF llama-quantize used for quant_gguf")
    ap.add_argument("--codes-dir", default=None,
                    help="cache finished Q2_K block bytes per tensor "
                         "(resume-safe); assemble when all present")
    ap.add_argument("--max-tensors", type=int, default=0,
                    help="chunk size (0 = no limit)")
    ap.add_argument("--no-feedback", action="store_true",
                    help="identity mode: grid fit on original weights, no "
                         "error propagation (must reproduce quant_gguf)")
    args = ap.parse_args()

    grams = Grams(args.gram_dir)
    imx = load_imatrix_llq(args.imatrix)
    ref, _ = index_2d(args.ref_gguf)
    rdr = GGUFReader(args.quant_gguf)
    arch = rdr.get_field("general.architecture").contents()
    codes = Path(args.codes_dir) if args.codes_dir else None
    if codes:
        codes.mkdir(parents=True, exist_ok=True)

    # phase A: compute (resume-safe)
    todo = []
    for t in rdr.tensors:
        shape = [int(d) for d in t.shape if int(d) > 1]
        if (t.tensor_type == gguf.GGMLQuantizationType.Q2_K
                and len(shape) == 2 and t.name in ref):
            n_out, n_in = shape[1], shape[0]
            if grams.S_blocks(t.name, n_in) is None:
                continue
            if t.name not in imx or imx[t.name].size != n_in:
                sys.exit(f"no imatrix entry of size {n_in} for {t.name} "
                         "(llama-quantize would have refused or used none)")
            todo.append((t, n_out, n_in))
    done = {}
    n_new = 0
    t0 = time.time()
    for i, (t, n_out, n_in) in enumerate(todo):
        cfile = codes / (t.name.replace("/", "_") + ".npy") if codes else None
        if cfile and cfile.exists():
            done[t.name] = cfile
            continue
        if args.max_tensors and n_new >= args.max_tensors:
            print(f"[chunk] limit reached ({n_new} new), rerun to continue",
                  flush=True)
            return
        ts = time.time()
        Wref = deq(ref[t.name])
        Hu = grams.Hu_blocks(t.name, n_in)
        raw, _ = freegrid_tensor(Wref, imx[t.name], Hu,
                                 feedback=not args.no_feedback)
        raw = raw.reshape(np.asarray(t.data).shape)
        if cfile:
            np.save(cfile, raw)
            done[t.name] = cfile
        else:
            done[t.name] = raw
        n_new += 1
        print(f"[freegrid] {i+1}/{len(todo)} {t.name} {n_out}x{n_in} "
              f"{time.time()-ts:.1f}s (total {time.time()-t0:.0f}s)",
              flush=True)

    # phase B: assemble, every other tensor copied verbatim
    w = GGUFWriter(args.out, arch)
    for key, f in rdr.fields.items():
        if key.startswith("GGUF.") or key == "general.architecture":
            continue
        sub = f.types[-1] if f.types[0] == gguf.GGUFValueType.ARRAY else None
        w.add_key_value(key, f.contents(), f.types[0], sub_type=sub)
    for t in rdr.tensors:
        if t.name in done:
            d = done[t.name]
            raw = np.load(d) if isinstance(d, Path) else d
            assert raw.shape == np.asarray(t.data).shape
            w.add_tensor(t.name, raw, raw_dtype=t.tensor_type)
        else:
            w.add_tensor(t.name, t.data, raw_dtype=t.tensor_type)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file(progress=False)
    w.close()
    print(f"wrote {args.out}: {len(done)} tensors free-grid, "
          f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
