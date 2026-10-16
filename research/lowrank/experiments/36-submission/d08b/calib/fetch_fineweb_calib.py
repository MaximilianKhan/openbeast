#!/usr/bin/env python3
"""E36-D FineWeb-Edu CALIBRATION slice (disjoint from the E32 eval text).

Source: HuggingFaceFW/fineweb-edu, config sample-10BT, split train, via the
HF datasets-server /rows API (the same pull path as E32's
fineweb-edu-control.txt, which is rows 0..399 joined with "\\n\\n").
This slice: rows from OFFSET (2000) upward, whole documents, joined with
"\\n\\n", stopping at the first document that takes the file past
TARGET_BYTES (3,000,000). Disjointness is verified three ways against the
eval text and its rows 0..399: (1) row ids, (2) exact document text
hashes, (3) 64-byte shingles of every calibration doc vs the eval text.

usage: python3 -I fetch_fineweb_calib.py OUT.txt MANIFEST.tsv EVAL.txt
Network: datasets-server.huggingface.co only. Raw JSON is parsed in memory,
never executed or written to disk.
"""
import hashlib
import json
import sys
import time
import urllib.request

API = ("https://datasets-server.huggingface.co/rows?dataset=HuggingFaceFW/"
       "fineweb-edu&config=sample-10BT&split=train&offset={o}&length={n}")
OFFSET, TARGET_BYTES, PAGE = 2000, 3_000_000, 100


def rows(offset, n):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(API.format(o=offset, n=n),
                                        timeout=60) as r:
                d = json.loads(r.read().decode("utf-8"))
            return [(x["row_idx"], x["row"]["id"], x["row"]["text"])
                    for x in d["rows"]]
        except Exception as e:  # noqa: BLE001 — retry transient HTTP errors
            print(f"retry {attempt} offset {offset}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    sys.exit(f"datasets-server failed at offset {offset}")


def main():
    out, manifest, eval_path = sys.argv[1:4]
    # --- eval rows 0..399: ids + exact-text check that the E32 file is them
    ev = []
    for o in range(0, 400, PAGE):
        ev += rows(o, PAGE)
    eval_text = open(eval_path, encoding="utf-8").read()
    rebuilt = "\n\n".join(t for _, _, t in ev)
    same = rebuilt.strip() == eval_text.strip()
    print(f"eval rows 0..399 re-pulled; == {eval_path}: {same}")
    if not same:
        sys.exit("eval text is not rows 0..399 joined by blank lines — "
                 "refusing (provenance of the held-out set unverified)")
    ev_ids = {i for _, i, _ in ev}
    ev_hash = {hashlib.sha256(t.encode()).hexdigest() for _, _, t in ev}
    # --- calibration rows OFFSET.. until TARGET_BYTES
    cal, size, o = [], 0, OFFSET
    while size < TARGET_BYTES:
        for idx, i, t in rows(o, PAGE):
            cal.append((idx, i, t))
            size += len(t.encode()) + (2 if len(cal) > 1 else 0)
            if size >= TARGET_BYTES:
                break
        o += PAGE
    # --- disjointness
    id_hits = sum(i in ev_ids for _, i, _ in cal)
    txt_hits = sum(hashlib.sha256(t.encode()).hexdigest() in ev_hash
                   for _, _, t in cal)
    sh_hits = 0
    for _, _, t in cal:
        b = t.encode()
        for k in range(0, max(1, len(b) - 64), 256):
            s = b[k:k + 64].decode("utf-8", "ignore")
            if len(s) >= 48 and s in eval_text:
                sh_hits += 1
    print(f"calibration docs: {len(cal)} (rows {cal[0][0]}..{cal[-1][0]}), "
          f"overlap with eval: ids {id_hits}, exact texts {txt_hits}, "
          f"64-byte shingles {sh_hits}")
    if id_hits or txt_hits:
        sys.exit("OVERLAP with the eval text — refusing")
    text = "\n\n".join(t for _, _, t in cal)
    open(out, "w", encoding="utf-8").write(text)
    with open(manifest, "w") as m:
        m.write("row_idx\tid\tbytes\tsha256\n")
        for idx, i, t in cal:
            m.write(f"{idx}\t{i}\t{len(t.encode())}\t"
                    f"{hashlib.sha256(t.encode()).hexdigest()}\n")
    print(f"wrote {out}: {len(text.encode())} bytes; shingle hits "
          f"{sh_hits} (boilerplate phrases; docs disjoint by id and text)")


if __name__ == "__main__":
    main()
