#!/usr/bin/env python3
"""E36-L tool audit (CPU only): do the qwen-era research tools handle
llama-arch tensor names?

1. writes SYNTHETIC Grams (anisotropic Wishart) for blocks < --layers, in
   exactly the layout the imatrix Gram patch writes: one file per hooked
   tensor (attn_k, attn_output, ffn_up, ffn_down), <name>.gram.bin, f32,
   blocked x8 when n > 8192 (LLAMA_IMATRIX_GRAM_MAXDIM default), manifest
   grams.json {name: {n, blocks, count}};
2. walks every 2D tensor of the BF16 model through the extractor's
   kind_of / rank-map / imatrix gate / gram_whitener and e13's s-source
   mapping, and prints the decision table.

Synthetic Grams are NOT measurement artifacts.
usage: audit_tools.py <bf16.gguf> <imatrix.gguf> <gram_out_dir> [--layers 2]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

EXP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EXP / "04-served-v0"))
sys.path.insert(0, str(EXP / "13-rerounder"))
import extract_adapter as ex  # noqa: E402
import e13_reround as e13     # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("bf16")
ap.add_argument("imatrix")
ap.add_argument("gram_dir")
ap.add_argument("--layers", type=int, default=2)
a = ap.parse_args()

HOOKED = ("attn_k.", "attn_output.", "ffn_up.", "ffn_down.")  # gram_wanted
ref, arch = ex.index_2d(a.bf16)
gd = Path(a.gram_dir)
gd.mkdir(parents=True, exist_ok=True)
man = {}
rng = np.random.default_rng(1)
T = 6144
for name, t in ref.items():
    m = re.match(r"blk\.(\d+)\.", name)
    if not m or int(m.group(1)) >= a.layers or \
            not any(h in name for h in HOOKED):
        continue
    n = int(t.shape[0])
    blocks = 8 if n > 8192 else 1
    bs = n // blocks
    scale = rng.lognormal(0, 1.0, n).astype(np.float32)
    g = np.empty(blocks * bs * bs, np.float32)
    for b in range(blocks):
        X = rng.standard_normal((T, bs), dtype=np.float32) * \
            scale[b * bs:(b + 1) * bs]
        g[b * bs * bs:(b + 1) * bs * bs] = (X.T @ X).ravel()
    g.tofile(gd / (name + ".gram.bin"))
    man[name] = {"n": n, "blocks": blocks, "count": T}
(gd / "grams.json").write_text(json.dumps(man))
print(f"synthetic grams: {sorted(man)}")

imat = ex.load_imatrix(a.imatrix)
rank_map = ex.parse_rank_map("ffn_gate=0,ffn_up=0,ffn_down=0")
gm = ex.load_grams(str(gd))
rows = []
for name, t in ref.items():
    n_in, n_out = int(t.shape[0]), int(t.shape[1])
    k = ex.kind_of(name)
    has_im = name in imat and len(imat[name]) == n_in
    r = rank_map.get(k, 128)
    target = has_im and r > 0
    # extractor gram source
    src = name
    for pat, fn in ex._GRAM_SOURCE:
        if re.search(pat, name):
            src = fn(name)
            break
    src13 = name
    for pat, fn in e13._GRAM_SOURCE:
        if re.search(pat, name):
            src13 = fn(name)
            break
    lay = re.match(r"blk\.(\d+)\.", name)
    wh = None
    if lay and int(lay.group(1)) < a.layers:
        wh = ex.gram_whitener(str(gd), gm, name, n_in)
    rows.append((name, k, n_in, n_out, has_im, target, src, src13 == src,
                 None if wh is None else (len(wh), wh[0][1].shape[0])))
for row in rows:
    if row[0].startswith("blk.0.") or not row[0].startswith("blk."):
        name, k, n_in, n_out, has_im, target, src, same, wh = row
        print(f"{name:28s} kind={k:8s} in={n_in:6d} out={n_out:6d} "
              f"imat={has_im!s:5s} target={target!s:5s} gram_src={src:28s} "
              f"e13_same={same} whitener={wh}")
tg = Counter(r[1] for r in rows if r[5])
print("extractor targets by kind:", dict(tg), "total", sum(tg.values()))
bad = [r for r in rows if r[0].startswith("blk.") and
       int(r[0].split(".")[1]) < a.layers and r[5] and r[8] is None]
print("targets in gram layers WITHOUT a whitener:", [r[0] for r in bad])
q2 = [r for r in rows if re.search(r"attn_[qk]\.|ffn_gate\.|ffn_up\.", r[0])
      and r[0].startswith("blk.") and int(r[0].split(".")[1]) < a.layers
      and r[8] is None]
print("Q2_K-coded kinds in gram layers WITHOUT a Gram (e13 would skip):",
      [r[0] for r in q2])
