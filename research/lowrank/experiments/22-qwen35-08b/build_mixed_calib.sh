#!/bin/bash
# Reproducible mixed-calibration corpus (round-2 repair of the unscripted gram08b-mixed).
# Held-out corpus = code-TEST (experiments/17-task-conditioned/code-test.txt); this uses
# only wiki-TRAIN and code-TRAIN, so the eval code-test stays provably held out.
set -euo pipefail
D=/home/max/Documents/openbeast
python3 - <<'PY'
w = open('/home/max/Documents/openbeast/research/lowrank/data/wikitext-2-raw/wiki.train.raw','rb').read()[:2000000]
c = open('/home/max/Documents/openbeast/research/lowrank/experiments/17-task-conditioned/code-train.txt','rb').read()[:1000000]
out = bytearray(); wi=ci=0
while wi<len(w) or ci<len(c):
    out += w[wi:wi+4000]; wi+=4000
    out += c[ci:ci+2000]; ci+=2000
open('/home/max/Documents/openbeast/research/lowrank/data/mixed-calib2.txt','wb').write(bytes(out))
print('mixed corpus bytes:', len(out))
PY
echo "SHA of inputs (provenance): wiki.train + code-train only; code-test NEVER included"
sha256sum $D/research/lowrank/data/mixed-calib2.txt
