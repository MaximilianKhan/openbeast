# TMLR submission checklist

Venue: **TMLR** (Transactions on Machine Learning Research), decided by the
author. Guidelines read 2026-10-15 on jmlr.org/tmlr: [author guide](https://jmlr.org/tmlr/author-guide.html),
[submissions](https://jmlr.org/tmlr/submissions.html), [editorial policies](https://jmlr.org/tmlr/editorial-policies.html),
[ethics](https://jmlr.org/tmlr/ethics.html), [acceptance criteria](https://jmlr.org/tmlr/acceptance-criteria.html),
[FAQ](https://jmlr.org/tmlr/faq.html), [reviewer guide](https://www.jmlr.org/tmlr/reviewer-guide.html).
Submission is through OpenReview: <https://openreview.net/group?id=TMLR>.

Status key: **done** (the build does it), **AUTHOR** (needs a decision or
text from the author), **check** (verify before uploading), **n/a**.

Page counts below are for the build of 2026-10-15 (`python3 build.py` prints
them on every build).

## Format

| item | rule (quoted or paraphrased from the pages above) | status |
|---|---|---|
| Style file | "Submissions must be PDF files generated using the TMLR LaTeX stylefile and template"; changes to "formatting, font, or layout" may be rejected without review | **done** — `tmlr.sty` loaded unmodified (vendored below); the generated preamble adds only amsmath/amssymb, microtype, graphicx, xcolor, booktabs/array/tabularx/longtable, hyperref (coloured links), url. Tables are set at `\small`, artifact tags at `\footnotesize` |
| Anonymous version | "submissions must be anonymized"; `\usepackage{tmlr}` with no option prints "Anonymous authors / Paper under double-blind review" | **done** — `paper-tmlr.pdf`; the `.tex` holds no author at all |
| Paper size, page numbers | US Letter, pages numbered | **done** (tmlr.sty) |
| Citations | natbib author-year with `tmlr.bst` | **done** — `refs.bib` from `refs.json`, `\citep`/`\citet`/`\citealp` chosen from context (latex_tmlr.natbib) |
| Appendix placement | "The submission PDF may include an Appendix, after the references" | **done** — main text, References, then Appendix A and B (the article/Typst builds keep A before the references) |
| Fonts / characters | tmlr.sty is pdfLaTeX-oriented (T1, Latin Modern) | **done** — the generated `.tex` is pure ASCII (all Unicode written as macros); compiled with Tectonic 0.17.0; the build fails on an unmapped character or a "Missing character" |
| Overfull boxes | — | **done** — 0 boxes > 1 pt in both PDFs |

## Length

| item | rule | status |
|---|---|---|
| Length | "Submissions may be any length, but a paper's length should be justified by its content"; "papers that are unusually long (not counting any Appendices) are likely to result in reviewing delays" | **AUTHOR** — main text is pp. 1–23 (ends mid p. 23), references pp. 23–26, appendix pp. 27–29; 29 pages in all |
| 12-page threshold | FAQ: "For submissions over 12 pages, we ask for reviews to be submitted within 4 weeks" (2 weeks otherwise); the reviewer guide counts main content before references | **AUTHOR** — at ~22.5 pages of main text the paper is on the 4-week track. Moving material (e.g. parts of §4, §6, §7) into the appendix is the lever; the sections are being rewritten anyway |

## Anonymity

| item | rule | status |
|---|---|---|
| Author block | anonymous | **done** |
| Artifact tags | must not reveal the project, user or repo | **done** — rendered `[Supp. E24]`; Appendix B ("Supplementary material index") lists locations relative to the supplementary archive, with the `research/lowrank/` prefix stripped |
| Mechanical scrub | names, URLs, local paths | **done** — `latex_tmlr.SCRUB`; the build greps the anonymous PDF's text and metadata for `latex_tmlr.FORBIDDEN` (OpenBeast, beast, Maximilian, Max, Khan, github.com, research/lowrank, /home/, maxjkh, passboards) after removing cited authors' names (a cited "Maximilian Kleinegger" is not the author) and fails on a hit. Result 2026-10-15: clean |
| Residue that only prose can fix | — | **AUTHOR** — the build prints the list (`latex_tmlr.RESIDUE`); see "Anonymization residue" below |
| Self-citation | an unpublished own project must not be cited by name | **check** — none found by name; the residue phrases are the indirect ones |
| Public copy of this paper | preprints are allowed ("You can still submit, even if a preprint of your work already is available online"); reviewers are asked not to search | **AUTHOR** — this paper's text, drafts and records are public on `main` of the author's GitHub repository (`research/lowrank/paper/final`, `research/lowrank/experiments`). That is permitted, but a distinctive sentence searched verbatim leads to the author. Decide whether that is acceptable; nothing to do if it is |
| PDF metadata | — | **done** — anonymous PDF: Title only, no Author; Creator "LaTeX with hyperref", Producer xdvipdfmx |
| Figures | — | **done** — `figures/*.pdf` checked: Matplotlib metadata only, no names or paths |

### Anonymization residue (for the author; not rewritten)

Phrases that identify nothing by themselves but point at the author's own
record or a repository reviewers cannot see. `python3 build.py` reprints
this list with current line numbers after every rewrite.

| location | phrase | note |
|---|---|---|
| `sections/02-method.md:68` (§2.5, Pre-registration) | "the project journal", "the project's own record", "second internal review" | fine if the journal ships in the anonymized supplement; otherwise say "a timestamped log in the supplementary material" |
| `sections/03-results-free-lever.md:47` | "the project's own literature notes" | describes a calibration corpus; keep or say "the authors' own notes" |
| `sections/03-results-free-lever.md:62` | "the project journal" | as above |
| `sections/04b-gsq-head-to-head.md:43` | "our journal" | as above |
| `sections/05-systems.md:9` | "raw benchmark output in the repository" | → "in the supplementary material" |
| `sections/06-limitations-future.md:23` | "raw output in the repository" | as above |
| `sections/06-limitations-future.md:25` | "the project's review notes and journal", "the project's own" | as above |
| `sections/06-limitations-future.md:55` (§7.3) | "in the project repository under `experiments/`" (path already scrubbed), "not in the repository" | → "supplementary material"; the paragraph is the natural place to say that code and data will be released with the camera-ready |
| `sections/06-limitations-future.md:57` (§7.3) | "build 0ef6e55ed" | a commit of the author's local llama.cpp tree; harmless unless that commit is on a public fork under the author's name |
| `sections/08-appendix-a.md:33` (A.5) | "The project's theory note" | ships as supplementary item `theory-alt`; keep or say "a note in the supplementary material" |

## Supplementary material

| item | rule | status |
|---|---|---|
| Size and form | "up to 100MB of supplementary material, such data, source code or illustrative videos"; PDF or ZIP | **AUTHOR** — the 30 tagged items are 301 git-tracked files, 15.3 MB (large binaries excluded, as §7.3 says) |
| Anonymized | "Like submissions, supplementary material must be anonymized" | **AUTHOR** — 113 of those 301 files contain an identifying string (OpenBeast, beast-rank, Max, github.com, /home/max …). The archive must be built with the layout of Appendix B (paths relative to `research/lowrank/`) and scrubbed; not built here |
| Authored by the submitters | "material created by the authors that directly supports the submission content" | **check** — `prior-art/MANIFOLD-CANDIDATES.md` (tag `manifolds`) is a literature note; confirm it holds no third-party text beyond quotation |

## Ethics, broader impact, LLM use

| item | rule | status |
|---|---|---|
| Broader Impact Statement | required "if their work carries a significant risk of harm"; otherwise optional; ethics guidelines expect "when applicable … a discussion about potential negative societal impacts"; the template places it as an unnumbered `\subsubsection*{Broader Impact Statement}` before the references | **AUTHOR** — the paper has none. The work (post-training quantization of open models) carries no evident risk, but one test model is an uncensored community finetune (heretic-v2 of Qwen3.6-27B, §1.1); a reviewer may ask. A short statement would go in the sections as `## Broader Impact Statement` at the end of the main text |
| LLM use | FAQ: allowed; "our expectation is that the ideas, claims and results found in submissions are human-sourced"; the use must be stated "as a footnote on the first page"; LLMs cannot be authors | **AUTHOR** — set `TMLR_FIRST_PAGE_NOTE` in `build.py` (Markdown); it becomes a `\thanks` footnote on the title in both builds. The build prints a reminder while it is unset |
| Human subjects / data | consent, PII, discredited datasets | **n/a** — public corpora only (wikitext-2, FineWeb-Edu, code); state in OpenReview's IRB field "none" |
| Acknowledgments, author contributions | only after acceptance | **n/a** now |

## Originality and dual submission

| item | rule | status |
|---|---|---|
| Original work | "TMLR only accepts original contributions that don't reuse the authors' own prior work" from archival venues; no expanded conference papers; arXiv and workshops are fine | **check** — the author confirms no archival version exists or is under review elsewhere |
| Licence | submissions are licensed CC BY 4.0, authors keep copyright | **AUTHOR** — accept on OpenReview |

## OpenReview metadata (hidden from reviewers)

| item | status |
|---|---|
| Complete OpenReview profile (affiliation, conflicts, publication history) for every author | **AUTHOR** |
| Action editor suggestions (emailed after submission; board: https://jmlr.org/tmlr/editorial-board.html) | **AUTHOR** |
| Funding, competing interests, non-institutional conflicts, IRB | **AUTHOR** |

## References

| item | status |
|---|---|
| All 78 cited arXiv ids in `refs.bib`, `eprint` + `archivePrefix` kept; venue where `refs.json` states one, else "arXiv preprint" | **done** |
| Three venues once withheld — **LoftQ** (ICLR 2024, oral), **LQ-LoRA** (ICLR 2024, poster), **Punica** (MLSys 2024, not SOSP) — confirmed 2026-10-22 against OpenReview and proceedings.mlsys.org and released from `venue_withheld`; seven more venues found the same day (MiLo, GlowQ, Sheared-LLaMA, Minitron, MPK, FLRQ, TileQ; `../../review/litsweep-2026-10-22/R6-serving-refs.md`) | **done** |
| Venue names expanded (`latex_tmlr.VENUES`); the three journal references (Gillis & Glineur 2011, Halko et al. 2011, CLoQ/TMLR 2025) written out in `latex_tmlr.JOURNALS`; the build stops if the arXiv record's text changes | **done** |
| "ICLR Workshop" (EoRA, 2410.21271) printed without a workshop name | **check** |
| Numbers attributed to cited works (`../references.md` §Verification flags) | **check** — unchanged by this build |

## Preprint version

| item | status |
|---|---|
| `paper-tmlr-preprint.pdf`: `\usepackage[preprint]{tmlr}`, author Max Khan, maximilian.khan@alumni.scu.edu; artifact tags name the public repository (Appendix B links it) | **done** |
| Affiliation line ("OpenBeast project" + repository URL, `TMLR_AUTHOR_BLOCK` in build.py) | **AUTHOR** — confirm; "Independent researcher" is the usual alternative |
| Posting the preprint during review is allowed; do not link it from the submission | note |

## Camera-ready (later)

`\usepackage[accepted]{tmlr}`, `\def\month{MM}`, `\def\year{YYYY}` and
`\def\openreview{\url{https://openreview.net/forum?id=XXXX}}` — not wired into
the build yet. The source archive is what `build.py` compiles from: the `.tex`,
`refs.bib`, `tmlr.sty`, `tmlr.bst` and `figures/*.pdf` in one directory.

## Vendored style files

From [JmlrOrg/tmlr-style-file](https://github.com/JmlrOrg/tmlr-style-file) at
commit `7bf90efe3a0debbba703c05c43f3ff7e4d4a2992` (2023-06-30, "Removed
duplicated line in tmlr.sty"), the head of `main` when cloned on 2026-10-15.
Unmodified:

| file | sha256 |
|---|---|
| `tmlr.sty` | `816214ff5919aa457b6b443bee52b15d9561421417b7f8a50cc84651519f0002` |
| `tmlr.bst` | `306fd454cf40771bee01293eeb98d2c1cd5f4e11ed0cd7296b335f354fc45206` |
| `LICENSE` (Apache-2.0, the repository's) | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` |

`tmlr.bst` carries its own LPPL header (it derives from natbib's bst).
The repository's `fancyhdr.sty` (v4.0.1, 2021) is not vendored: Tectonic's
bundle and any TeX Live provide fancyhdr, and the build compiles cleanly with
it. `math_commands.tex` (optional notation macros) is not used.
