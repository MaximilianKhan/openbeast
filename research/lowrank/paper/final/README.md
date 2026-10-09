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
| `build.py` | assembles the sections, turns `[source: …]` markers into artifact tags and `arXiv:` ids into a numbered reference list (entries from `refs.json`), writes `paper.typ`, compiles |
| `fetch_refs.py`, `refs/`, `refs.json` | the bibliographic record: `refs/arxiv-*.xml` are the saved arXiv API responses, `refs/review.json` the hand-kept cross-check outcome, `refs.json` what `fetch_refs.py` derives from them (`--fetch` re-downloads) |
| `strip_notes.py` | how `sections/` was first derived from `../draft/` (one-time) |

Rebuild:

```sh
python3 figures.py
mise exec typst@0.15.1 -- python3 build.py     # or any typst ≥ 0.13 on PATH
```

`../draft/` is the working draft with its dated revision notes; it is kept
as history and is no longer the current text.

References: every cited arXiv id (50) resolves, and each record's title was
read against the sentence that cites it and its row in `../references.md` —
no mismatch (2026-10-09). Authors, title and year are arXiv's; a venue is
printed only where the arXiv record or `../references.md` states one, and
`refs.json` keeps the sentence it was read from. An id that `refs.json` does
not verify falls back to its short name with a trailing "(unverified)". After
citing a new id, run `python3 fetch_refs.py --fetch`, check the new title
against the text, then rebuild.

Before a formal submission: three entries print as arXiv preprints because
their only venue statement is soft (`refs/review.json`, `venue_withheld`:
LoftQ, LQ-LoRA, Punica — confirm against the proceedings), the check above
covers each work's identity, not the specific numbers the text attributes
to it (`../references.md` §Verification flags still stands), and the author
line and venue are the author's to settle.
