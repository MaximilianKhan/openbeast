# Supplementary archive (TMLR, anonymous)

`build_supp.py` builds the anonymized supplementary material for the TMLR
submission from the repository as it stands. Rebuild whenever the cited
experiments change (E36 is still being written):

```sh
cd research/lowrank/paper/final/supplementary
nice -n 19 python3 build_supp.py
```

It writes:

| output | what | in git |
|---|---|---|
| `build/supplementary/` | the staged archive tree | no (`.gitignore`) |
| `build/supplementary.zip` | the file to upload to OpenReview (≤ 100 MB; the build fails above 95 MB) | no |
| `MANIFEST.md` | items ↔ paper sections, what was excluded and why; also copied into the archive | yes |

Exit status 0 means the gate passed. The build reads files only. It never
runs llama.cpp or touches the GPU, and it takes a few seconds of CPU.

## What goes in (by rule)

- **Items**: the `[source: …]` markers in `../sections/*.md`, parsed with
  `build.py`'s own `sources()`/`tag_for()`. The item set and paths are therefore
  exactly Appendix B ("Supplementary material index") of `paper-tmlr.pdf`, with
  `research/lowrank/` stripped. A newly cited experiment shows up on the next
  build without any change here. The other item is `kernels/` (the
  research-side llama.cpp patches and their README). `upstream/` holds only
  issue drafts, not patches, so it is not included.
- **Files inside an item**: types `.py .sh .md .txt .log .json .csv .tsv
  .patch .diff` plus `SHA256SUMS`, each at most 5 MB. Directories named
  `codes*`, `*cache*`, `__pycache__` or `.*` are skipped, and so are symlinks.
  `.txt` files of 100 KB or more that are named like a corpus (`calib`, `train`,
  `test`, `control`, `wiki`, `fineweb`, `mix<N>` …) or sit in a `calib/`
  directory are skipped as copies of public corpora (their sha256 is listed in
  the manifest).

## Scrub mapping (`SCRUB` in build_supp.py, applied to text and file names)

| original | becomes |
|---|---|
| `/home/max/Documents/openbeast[-research][/.wt-*]/research/lowrank` | `<ROOT>` (the archive root) |
| `/home/max/Documents/openbeast[-research][/.wt-*]` | `<REPO>` |
| `/home/max`, `~/` | `<HOME>` |
| `research/lowrank` (relative, also `$VAR/research/lowrank`) | `<ROOT>` |
| in `.sh` files: `<ROOT>`, `<REPO>`, `<HOME>` | `${SUPP_ROOT}`, `${SUPP_REPO}`, `${HOME}` (so `bash -n` passes) |
| `-home-max-` (in session-dir names) | `-home-user-` |
| `From: <name> <email>` (format-patch header) | `From: Anonymous Authors <anonymous@example.invalid>` |
| author emails (gmail, scu.edu) | `<email withheld>` |
| github.com / huggingface.co URLs of `MaximilianKhan` / `maxjkh` | `<repository withheld>` |
| `MaximilianKhan`, `maxjkh` | `<author>` |
| `Maximilian Khan`, `Max Khan`, `Maximilian` (not "Maximilian Kleinegger"), `Khan`, `Max`, `Max's`, `MK` | `the author` / `The author` / `the author's` |
| `OPENBEAST` / `OpenBeast` / `openbeast` | `SERVESTACK` / `ServeStack` / `servestack` |
| `beast-rank`, `beast_rank`, `beastrank` (any case) | `lrk` (`LRK` if upper case) |
| `beastup` / `beastdown` | `stack-up` / `stack-down` |
| `beast-<x>`, `beast_<x>`, `BEAST_<x>` (beast-slot, beast-gate, …) | `stack-<x>`, `stack_<x>`, `STACK_<x>` |
| bare `beast` / `Beast` / `BEAST` (the workstation's host name) | `rig` / `Rig` / `RIG` |
| `maximilians-*`, `iphone<N>`, `*.ts.net`, `omarchy` | `<host>` |
| Tailscale CGNAT addresses `100.64–127.x.y` | `<tailnet-ip>` |
| GPU UUIDs, MAC addresses | `GPU-<uuid>`, `<mac>` |
| hex tokens (7–40 chars) that prefix a commit of the author's repositories (`git rev-list --all` + reflog of this repo, local-only commits of `llama.cpp/`, the patches' `From <sha>` lines) | `<commit>` |

These stay unchanged: upstream llama.cpp build numbers (`b10865`), upstream or
third-party commit hashes (`0ef6e55ed`, `d4389a4dd`, GSQ `03fc164`), public
model names (heretic, Qwen …), and cited authors' names.

## The gate

`latex_tmlr.FORBIDDEN` plus `EXTRA_FORBIDDEN` (maximilian, maxjkh, scu.edu,
passboards, /home/max, home-max, maximilians-, iphone, omarchy, .ts.net, MK,
@gmail.com, tailnet IPs). It runs on every file's text and every path in the
archive, plus a check that no own-commit hash is left. Before it runs, cited
authors' names (from `../refs.json`) and `github.com/<owner>` links whose
owner is in `ALLOWED_GITHUB` (upstream and third-party) are masked. A GitHub
owner not on that list fails the gate. Every `.py` must still compile and
every `.sh` must pass `bash -n`. Any failure prints the hit with its file
and line, and the build exits 1.

Further rules on references into the repository:

| original | becomes |
|---|---|
| `docs/<NAME>.md` (with any `../` prefix), `AGENTS.md` | `<project notes, not included>` |
| eval-harness result files `eval-<row>-YYYYMMDD-HHMMSS.json`, `eval-<row>-*.json` | `<eval result file>` |

## Targeted archive edits (`ARCHIVE_EDITS`)

Prose that the token scrub leaves reading as one person directing agents
("the author directive", "his dream", quoted instructions) is reworded by an
explicit table: archive path → (exact scrubbed old text, new text, expected
count). The edits apply to the archive copy only; the originals are never
touched. The build fails if an old text does not occur exactly the expected
number of times, or if a file in the table is missing from the archive. When
a source file changes under one of these lines, update the row. The same
table replaces the author's own repository PR numbers (#22, #35, #45, #56,
#61, #62) with `<PR>`. Upstream llama.cpp numbers (#28068, #25489, #21037,
#8831, #23575, …) stay. It also rewords the E17 README's line saying that its
code corpus came from the authors' own repositories.

The gate also checks for `#22|#35|#45|#56|#61|#62`, `docs/*.md`, `AGENTS.md`,
eval result file names, "the author directive", "the author-ordered" and
`his`/`His`.

## Corpora

The E17 code corpus (`code-train/test/calib.txt`) was cut from the authors'
own source tree. It is withheld for anonymity, not for size. The other
excluded corpora are public, and `MANIFEST.md` names the included script that
rebuilds and sha256-checks each one.

## Accepted risk (decided)

The paper's records (JOURNAL, experiment READMEs) are also public verbatim
in the author's GitHub repository, so a reviewer who searches a distinctive
sentence can find the author. TMLR allows preprints, and this risk is accepted.

## Not mechanical: still in the archive

Harness and code names that describe the setup but are not files of the
archive: `agents/runner.py`, `evals/cache.py`, `run_eval.py`,
`SERVESTACK_EVAL`, `evals/results/` (`capability-verdict-iq3*.txt`,
`e32_cap_verdict.py`, `36-submission/f_capability.sh`). There is also the
JOURNAL 2026-09-08 "DOCTRINE" entry about a multi-language eval leaderboard
(zig).
