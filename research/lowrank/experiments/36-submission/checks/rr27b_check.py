#!/usr/bin/env python3
"""Independent check of the 27B re-round (E36-B): dequantize BF16, the bare
Q2_K and the re-rounded Q2_K with gguf-py's own dequantizer (not the sweep's
codec) and compute, per tensor, the unweighted (Frobenius) error, the error
whitened by a calibration Gram, and the share of changed codes.
Output of the 2026-10-15 run: rr27b-independent-dequant.txt.
usage: rr27b_check.py [tensor ...]   (run from the repository root)"""
import json, sys
import numpy as np
sys.path.insert(0, 'llama.cpp/gguf-py')
from gguf import GGUFReader
from gguf.quants import dequantize
R = 'research/lowrank/experiments/'
bf = GGUFReader('weights/research-staging/Qwen3.6-27B-uncensored-heretic-v2-Native-MTP-Preserved-BF16.gguf')
q = GGUFReader(R + '27-bf16-rederivation/h27bf16-Q2_K.gguf')
rr = GGUFReader(R + '36-submission/h27bf16-Q2K-rr-wiki.gguf')
G = 'research/lowrank/data/gram27b-e36-wiki/'
man = json.load(open(G + 'grams.json'))
def T(r, n):
    t = [x for x in r.tensors if x.name == n][0]
    return dequantize(t.data, t.tensor_type).reshape(-1, int(t.shape[0])).astype(np.float64)
names = sys.argv[1:] or ['blk.3.attn_k.weight', 'blk.30.ffn_up.weight', 'blk.10.ssm_out.weight']
for n in names:
    W, A, B = T(bf, n), T(q, n), T(rr, n)
    m = man[n]
    g = np.fromfile(G + n + '.gram.bin', dtype=np.float32).reshape(m['n'], m['n']).astype(np.float64) / m['count']
    f = lambda E: np.einsum('ij,jk,ik->', E, g, E)
    ea, eb = W - A, W - B
    print(n, W.shape, 'frob bare %.4g rr %.4g | whitened bare %.4g rr %.4g ratio %.3f | codes changed %.1f%%'
          % (np.linalg.norm(ea), np.linalg.norm(eb), f(ea), f(eb), f(eb) / f(ea), 100 * np.mean(A != B)))
