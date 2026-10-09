# The paper

`paper.pdf` — *The Free Lever and the Measured Walls: Re-Rounding, Whitened
Low-Rank Correction, and the Limits of Post-Training Quantization Repair in
llama.cpp.*

| file | what it is |
|---|---|
| `paper.pdf` | the paper |
| `paper.md` | the same text as one Markdown file |
| `sections/*.md` | the source of truth; edit these |
| `figures.py`, `figures/` | the two figures; every number is copied from a named results file |
| `build.py` | assembles the sections, turns `[source: …]` markers into artifact tags and `arXiv:` ids into a numbered list, writes `paper.typ`, compiles |
| `strip_notes.py` | how `sections/` was first derived from `../draft/` (one-time) |

Rebuild:

```sh
python3 figures.py
mise exec typst@0.15.1 -- python3 build.py     # or any typst ≥ 0.13 on PATH
```

`../draft/` is the working draft with its dated revision notes; it is kept
as history and is no longer the current text.

Before a formal submission: the reference list carries short names and
arXiv numbers, not full bibliographic entries, and the author line and
venue are the author's to settle.
