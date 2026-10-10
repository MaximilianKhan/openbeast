# The paper

`paper.pdf` — *The Free Lever and the Measured Walls: Re-Rounding, Whitened
Low-Rank Correction, and the Limits of Post-Training Quantization Repair in
llama.cpp.*

| file | what it is |
|---|---|
| `paper.pdf` | the paper (Typst build) |
| `paper.tex`, `paper-latex.pdf` | the same paper as one self-contained LaTeX file, and its compiled proof |
| `paper.md` | the same text as one Markdown file |
| `sections/*.md` | the source of truth; edit these |
| `figures.py`, `figures/` | the two figures, as `.svg` (Typst) and `.pdf` (LaTeX); every number is copied from a named results file |
| `build.py` | assembles the sections, turns `[source: …]` markers into artifact tags and `arXiv:` ids into a numbered reference list (entries from `refs.json`), writes `paper.typ` and `paper.tex`, compiles both |
| `latex.py` | the LaTeX backend `build.py` calls (Markdown subset → LaTeX, table layout, §-reference links, log check) |
| `fetch_refs.py`, `refs/`, `refs.json` | the bibliographic record: `refs/arxiv-*.xml` are the saved arXiv API responses, `refs/review.json` the hand-kept cross-check outcome, `refs.json` what `fetch_refs.py` derives from them (`--fetch` re-downloads) |
| `strip_notes.py` | how `sections/` was first derived from `../draft/` (one-time) |

Rebuild:

```sh
python3 figures.py                             # only when a figure changes
mise exec typst@0.15.1 -- python3 build.py     # or any typst ≥ 0.13 on PATH
```

That one command writes `paper.md`, `paper.typ`, `paper.pdf`, `paper.tex` and
`paper-latex.pdf`. `--no-latex` skips the LaTeX half; `--keep-log` leaves the
TeX log in `paper-latex.log`. Without Tectonic it still writes `paper.tex`
and says so.

### The LaTeX build

`paper.tex` is generated — edit `sections/*.md`, never the `.tex`. It uses
the article class and standard packages only (geometry, fontspec,
amsmath/amssymb, microtype, graphicx, xcolor, booktabs, array, tabularx,
longtable, enumitem, hyperref) and needs a Unicode engine, because the text's
symbols (−, ×, ≈, σ, Δ, →, ‖, H̃ …) are passed through as UTF-8. Fonts are
Libertinus Serif and DejaVu Sans Mono, loaded by file name from the TeX
distribution; the two characters they lack (ᵀ, ≳) are mapped in
`latex.UNICODE`. The build fails if the TeX log reports a "Missing
character", and prints every overfull box wider than 1 pt.

Engine: **Tectonic 0.17.0** (no TeX is installed system-wide; Tectonic is one
static binary and needs no root). `build.py` looks for `$TECTONIC`, then
`tectonic` on PATH, then `~/.local/share/openbeast/tectonic/tectonic`:

```sh
mkdir -p ~/.local/share/openbeast/tectonic && cd ~/.local/share/openbeast/tectonic
f=tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz
curl -sLO https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/$f
echo "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7  $f" | sha256sum -c
tar xzf $f && ./tectonic --version
```

The sha256 is the digest GitHub publishes for that release asset (checked
2026-10-09). The first compile downloads the packages and fonts it needs
into `~/.cache/tectonic` (network, about a minute); later ones reuse the cache
in seconds. mise's registry has no Tectonic. Tectonic names its output after
the input, which would overwrite the Typst `paper.pdf`, so `build.py`
compiles into a temporary directory and copies the result to
`paper-latex.pdf`; by hand that is
`tectonic --outdir /tmp/x paper.tex`. With a full TeX Live,
`xelatex -jobname=paper-latex paper.tex` run twice should give the same
document — not tried here.

What differs from the Typst PDF, by construction: section headings are
starred and keep the numbers written in the Markdown (LaTeX's counters
cannot produce "4.3b"), every "§x.y" is a link to its heading and the build
stops on one that has no heading; citations are `\cite` into a
`thebibliography` in first-citation order, so the numbers are the Typst
build's; figures float; the figure PDFs embed DejaVu Sans and break their y
label over two lines (the SVGs are set by Typst in the document font).

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
