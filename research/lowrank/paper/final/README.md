# The paper

`paper.pdf` — *What Post-Training Repair Buys on llama.cpp Quantization: A Paired Audit on
and off the Calibration Distribution.*

## Status (2026-10-22)

Venue TMLR, double-blind. The text is complete except for the capability
replication (E36-F, running; `../../experiments/36-submission/README.md`):
six `[PENDING:F]` markers remain by design, in the abstract, §1.3, §5.5, §5.6,
§7.2 and §7.5, and `build.py --final` refuses to build while any remains. A
pre-final numbers audit traced every quantitative claim to its results file
and was applied on 2026-10-22 (commit de6ee1a; what it changed is listed in
`../../review/response-2026-10-16.md`). What remains, in order:

1. When F completes: `python3 f_analysis.py` and `--md` in
   `../../experiments/36-submission/`, then fill the six markers by the
   procedure in `F-DRAFT.md` there.
2. A final numbers audit of the filled text.
3. `mise exec typst@0.15.1 -- python3 build.py --final`.
4. Rebuild the supplementary archive: `nice -n 19 python3 supplementary/build_supp.py`.
5. Delete the 73.5 GB A-full reference
   (`research/lowrank/data/e36-bf16ref27b-wiki-full.logits`), open the PR to
   main; the author submits on OpenReview.

| file | what it is |
|---|---|
| `paper.pdf` | the paper (Typst build) |
| `paper.tex`, `paper-latex.pdf` | the same paper as one self-contained LaTeX file, and its compiled proof |
| `paper.md` | the same text as one Markdown file |
| `paper-tmlr.tex`, `paper-tmlr.pdf` | the **TMLR anonymous submission** (`\usepackage{tmlr}`), see below |
| `paper-tmlr-preprint.tex`, `paper-tmlr-preprint.pdf` | the same with `\usepackage[preprint]{tmlr}` and the author named |
| `refs.bib` | generated from `refs.json` for the TMLR builds (natbib, `tmlr.bst`) |
| `latex_tmlr.py` | the TMLR backend `build.py` calls (ASCII output, natbib citations, anonymization) |
| `tmlr/` | the vendored TMLR style (`tmlr.sty`, `tmlr.bst`, LICENSE) and `SUBMISSION.md`, the submission checklist with each item's status |
| `sections/*.md` | the source of truth; edit these |
| `figures.py`, `figures/` | the two figures, as `.svg` (Typst) and `.pdf` (LaTeX); every number is copied from a named results file |
| `build.py` | assembles the sections, turns `[source: …]` markers into artifact tags and `arXiv:` ids into a numbered reference list (entries from `refs.json`), writes `paper.typ` and `paper.tex`, compiles both |
| `latex.py` | the LaTeX backend `build.py` calls (Markdown subset → LaTeX, table layout, §-reference links, log check) |
| `fetch_refs.py`, `refs/`, `refs.json` | the bibliographic record: `refs/arxiv-*.xml` are the saved arXiv API responses, `refs/review.json` the hand-kept cross-check outcome, `refs.json` what `fetch_refs.py` derives from them (`--fetch` re-downloads) |
| `strip_notes.py` | how `sections/` was first derived from `../draft/` (one-time) |
| `supplementary/` | `build_supp.py`, the anonymized supplementary-archive builder (output in `supplementary/build/`, file list in `MANIFEST.md`) |

Rebuild:

```sh
python3 figures.py                             # only when a figure changes
mise exec typst@0.15.1 -- python3 build.py     # or any typst ≥ 0.13 on PATH
```

That one command writes `paper.md`, `paper.typ`, `paper.pdf`, `paper.tex`,
`paper-latex.pdf`, and the TMLR set (`refs.bib`, `paper-tmlr.tex/.pdf`,
`paper-tmlr-preprint.tex/.pdf`). It warns on any `[PENDING]` marker; `--final`
makes that an error (the submission build). `--no-latex` skips both LaTeX halves;
`--no-tmlr` skips only the TMLR set; `--keep-log` leaves the TeX logs in
`paper-latex.log`, `paper-tmlr.log` and `paper-tmlr-preprint.log`. Without
Tectonic it still writes the `.tex` files and says so.

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

### The TMLR build

Venue: TMLR. `tmlr/SUBMISSION.md` is the checklist against the current
author guidelines, with what the build does and what the author still has to
decide; read it before uploading.

`latex_tmlr.py` reuses `latex.py`'s translation and changes five things:

- **Style.** `tmlr.sty` from `tmlr/` (JmlrOrg/tmlr-style-file @ 7bf90efe3a0d,
  unmodified) sets fonts, geometry, headings and natbib author-year
  citations; the preamble adds no layout of its own. Appendix A and B follow
  the references, as TMLR asks.
- **Characters.** `tmlr.sty` is a pdfLaTeX style (T1, Latin Modern), so the
  generated `.tex` is pure ASCII: every non-ASCII character (−, ×, ≈, σ, Δ,
  →, ‖, H̃, ᵀ, ≳, curled quotes, accented author names …) is written as a
  macro from `latex_tmlr.ASCII`. An unmapped character stops the build; a
  "Missing character" in the log fails it, as in the article build. The same
  file should compile with `pdflatex` + `bibtex` (not tried; no TeX Live here).
- **Citations.** `refs.bib` is generated from `refs.json` (all 78 entries,
  key `arxiv<id>`, `eprint`/`archivePrefix` kept; any
  `venue_withheld` entry prints as an arXiv preprint and is commented in the
  file; none is withheld since 2026-10-22). Markers become `\citep` when a parenthesis holds only citations,
  `\citet` when the id is used as a noun ("the convention of arXiv:X"), and
  `\citealp` inside a parenthesis with other text.
- **Anonymization** (`paper-tmlr` only). Artifact tags render as
  `[Supp. E24]`; Appendix B maps them to paths inside the supplementary
  archive (`research/lowrank/` stripped). `latex_tmlr.SCRUB` removes names,
  GitHub URLs and local paths. After compiling, the build greps the PDF's text
  and metadata for `latex_tmlr.FORBIDDEN` and fails on a hit, and it prints
  the residue that only prose can fix (`latex_tmlr.RESIDUE`, with section
  file and line).
- **First-page note.** `TMLR_FIRST_PAGE_NOTE` in `build.py` becomes a
  footnote on the title (TMLR wants the LLM-use disclosure there); unset, the
  build prints a reminder. The preprint's author block is `TMLR_AUTHOR_BLOCK`.

Compiling: `build.py` copies the `.tex`, `refs.bib`, `tmlr.sty`, `tmlr.bst`
and `figures/*.pdf` into a temporary directory (exactly a source archive) and
runs Tectonic there; Tectonic runs bibtex itself. It reports page numbers of
the main text, references and appendix (from labels in the `.aux`), overfull
boxes over 1 pt, undefined citations, and bibtex warnings. By hand:

```sh
d=$(mktemp -d); cp paper-tmlr.tex refs.bib tmlr/tmlr.sty tmlr/tmlr.bst $d; cp -r figures $d
(cd $d && ~/.local/share/openbeast/tectonic/tectonic paper-tmlr.tex)
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

Before a formal submission: the three venues once withheld (LoftQ, LQ-LoRA,
Punica) were confirmed against OpenReview and the MLSys proceedings on
2026-10-22 and now print; the check above covers each work's identity, not the specific numbers the text attributes
to it (`../references.md` §Verification flags still stands). The author line
is settled (2026-10-15): Max Khan, Independent Researcher, in the preprint
build only; the TMLR build is anonymous. The venue is TMLR (see "The TMLR build").
