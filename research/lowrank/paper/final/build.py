#!/usr/bin/env python3
"""Assemble sections/*.md into paper.md and paper.typ, then compile paper.pdf.

    python3 build.py            # needs typst (e.g. `mise exec typst@0.15.1 -- python3 build.py`)
    python3 build.py --no-latex # skip paper.tex / paper-latex.pdf
    python3 build.py --keep-log # also leave the TeX log in paper-latex.log

paper.tex is written by latex.py (the LaTeX backend) and compiled to
paper-latex.pdf when Tectonic is found: $TECTONIC, `tectonic` on PATH, or
~/.local/share/openbeast/tectonic/tectonic. See README.md.

The Markdown sections are the source of truth. This script only re-shapes
them: `[source: path]` markers become short artifact tags (Appendix B maps
tags back to paths), `arXiv:NNNN.NNNNN` becomes a numbered reference, and the
Markdown subset the sections use is translated to Typst.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile

import latex

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent.parent          # research/lowrank
TITLE = ("Frozen-Grid Re-Rounding and the Limits of Low-Rank Correction for "
         "llama.cpp Quantization: A Paired Audit")
AUTHOR = "Maximilian Khan"
AFFIL = "OpenBeast project · github.com/MaximilianKhan/openbeast"
DATE = "October 2026"

NAMED = {"RESULTS_ROLLUP.md": "rollup", "JOURNAL.md": "journal", "PROTOCOL.md": "protocol",
         "TODO.md": "todo", "DEPLOYABLE-WINS.md": "wins", "ABLATION-PLAN.md": "ablation",
         "theory-L6-family-subsumption.md": "theory-L6",
         "theory-alternation-convergence.md": "theory-alt", "MANIFOLD-CANDIDATES.md": "manifolds"}
EXTRA_NAMES = {"2604.18556": "GSQ", "2608.07019": "ReQuant", "2608.15567": "SchurQuant",
               "2505.22988": "YAQA", "2608.23144": "AWSRC", "2605.00649": "RCO"}
tags = {}                           # tag -> repo path


def tag_for(piece):
    piece = piece.strip()
    m = re.search(r"experiments/(\d+[a-z]?)-([\w-]+)", piece)
    if m:
        t = "E" + m.group(1).lstrip("0")
        tags[t] = f"research/lowrank/experiments/{m.group(1)}-{m.group(2)}/"
        return t
    if "review/" in piece:
        tags["review"] = "research/lowrank/review/"
        return "review"
    for name, t in NAMED.items():
        if name in piece:
            hit = next(ROOT.rglob(name), None)
            tags[t] = str(hit.relative_to(ROOT.parent.parent)) if hit else name
            return t
    return None


def sources(text):
    def rep(m):
        out = []
        for piece in re.split(r"[;,]", m.group(1)):
            t = tag_for(piece)
            if t and t not in out:
                out.append(t)
        return "\x05" + ", ".join(out) + "\x06" if out else ""
    return re.sub(r"\s*\[source:([^\]]*)\]", rep, text)


def load_refs():
    names = {}
    for line in (HERE.parent / "references.md").read_text().splitlines():
        m = re.match(r"\|\s*(\d{4}\.\d{4,5})\s*\|\s*([^|]+)\|", line)
        if m:
            names.setdefault(m.group(1), m.group(2).strip())
    return names


def load_bib():
    """refs.json (written by fetch_refs.py from the saved arXiv API responses), by id."""
    f = HERE / "refs.json"
    return {e["id"]: e for e in json.loads(f.read_text())} if f.exists() else {}


def md_plain(s):
    """Bibliographic text must reach the page literally.

    esc() already protects Typst's own special characters; the four below are
    the ones inline() reads as Markdown first, so a title carrying one would be
    silently restyled. None does today - stop the build if that changes.
    """
    if re.search(r"[*`^|]", s):
        sys.exit(f"build.py: reference text contains Markdown-active characters, handle it: {s!r}")
    return s


def ref_parts(i, bib, names):
    """(arXiv id, text before "arXiv:ID", text after it) - one reference, unformatted.

    `Authors. Title. Venue or "arXiv preprint", year. arXiv:ID.`
    An id without a verified record keeps the old short-name form, marked
    (unverified), so a guess can never pass for a checked entry.
    """
    e = bib.get(i)
    if not e or not e.get("verified"):
        name = names.get(i, "").strip()
        return i, (md_plain(name) + ". " if name else ""), ". (unverified)"
    a = e["authors"]
    who = ", ".join(a) if len(a) <= 4 else ", ".join(a[:3]) + ", et al"
    if e.get("venue") and e.get("venue_year"):
        where = f"{e['venue']}, {e['venue_year']}"
    elif e.get("venue"):
        where = e["venue"].rstrip(".")           # a journal reference carries its own date
    else:
        where = f"arXiv preprint, {e['year']}"
    return i, f"{md_plain(who)}. {md_plain(e['title'].rstrip('.'))}. {md_plain(where)}. ", "."


def ref_entry(n, i, bib, names):
    _, before, after = ref_parts(i, bib, names)
    return f"[{n}] {before}arXiv:{i}{after}"


refs = []


def cite(text):
    def rep(m):
        i = m.group(1)
        if i not in refs:
            refs.append(i)
        return latex.CITE0 + i + latex.CITE1
    return re.sub(r"arXiv:(\d{4}\.\d{4,5})", rep, text)


def numbered(text):
    """Citation markers -> "[n]", for the Markdown and Typst outputs (LaTeX uses \\cite)."""
    return re.sub(latex.CITE0 + "(.*?)" + latex.CITE1, lambda m: f"[{refs.index(m.group(1)) + 1}]", text)


# ---------------------------------------------------------------- md -> typst
def esc(s):
    return re.sub(r"([\\#$@<>_*~`\[\]])", r"\\\1", s)


def inline(s):
    codes = []
    s = re.sub(r"`([^`]*)`", lambda m: codes.append(m.group(1)) or f"\x07{len(codes) - 1}\x07", s)
    s = re.sub(r"\*\*(.+?)\*\*", "\x01\\1\x02", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", "\x03\\1\x04", s)
    s = re.sub(r"\^\{([^}]*)\}", "\x08\\1\x09", s)
    s = re.sub(r"\^([−-]?[\d.]*\d)", "\x08\\1\x09", s)
    s = s.replace("\\|", "|")
    s = esc(s)
    for a, b in (("\x01", "#strong["), ("\x02", "]"), ("\x03", "#emph["), ("\x04", "]"),
                 ("\x05", "#src["), ("\x06", "]"), ("\x08", "#super["), ("\x09", "]")):
        s = s.replace(a, b)
    return re.sub(r"\x07(\d+)\x07", lambda m: "#raw(\"" + codes[int(m.group(1))].replace("\\", "\\\\").replace('"', '\\"') + "\")", s)


def cells(row):
    return [c.strip() for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))]


def to_typst(md):
    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("#"):
            level = len(ln) - len(ln.lstrip("#"))
            out.append("=" * level + " " + inline(ln[level:].strip()) + "\n")
            i += 1
        elif ln.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s:|-]+\|$", lines[i + 1].strip()):
            head, rows = cells(ln), []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i])); i += 1
            n = len(head)
            body = ", ".join(f"[#strong[{inline(c)}]]" for c in head)
            for r in rows:
                body += ",\n  " + ", ".join(f"[{inline(c)}]" for c in (r + [""] * n)[:n])
            out.append(f"#tbl({n}, {body})\n")
        elif ln.startswith("!["):
            m = re.match(r"!\[(.*)\]\((.*)\)", ln)
            out.append(f"#figure(image(\"{m.group(2)}\", width: 88%), caption: [{inline(re.sub(r"^Figure \d+\.\s*", "", m.group(1)))}])\n")
            i += 1
        elif ln.startswith("- "):
            while i < len(lines) and lines[i].startswith("- "):
                out.append("- " + inline(lines[i][2:])); i += 1
            out.append("")
        else:
            out.append(inline(ln) + "\n")
            i += 1
    return "\n".join(out)


PREAMBLE = r"""
#set document(title: "%(title)s", author: "%(author)s")
#set page(paper: "us-letter", margin: (x: 1.05in, y: 1in), numbering: "1")
#set text(font: ("Libertinus Serif", "New Computer Modern"), size: 10pt, lang: "en")
#set par(justify: true, leading: 0.6em, spacing: 0.95em)
#show heading.where(level: 1): it => block(above: 1.6em, below: 0.8em, text(size: 13pt, it.body))
#show heading.where(level: 2): it => block(above: 1.3em, below: 0.6em, text(size: 11pt, it.body))
#show raw: set text(size: 8.5pt)
#show figure.caption: set text(size: 8.5pt)
#let src(body) = text(size: 7.5pt, fill: luma(110))[ \[#body\]]
#let tbl(n, ..c) = figure(kind: "plain-table", supplement: none, block(width: 100%%, {
  set text(size: 8.5pt)
  set par(justify: false, leading: 0.45em)
  table(columns: n, stroke: none, align: left + horizon, inset: (x: 5pt, y: 3.5pt),
    table.hline(stroke: 0.7pt), ..c.pos().slice(0, n), table.hline(stroke: 0.4pt),
    ..c.pos().slice(n), table.hline(stroke: 0.7pt))
}))
#align(center)[
  #text(size: 15pt, weight: "bold")[%(title)s]
  #v(0.7em)
  #text(size: 11pt)[%(author)s] \
  #text(size: 9pt, fill: luma(80))[%(affil)s] \
  #text(size: 9pt, fill: luma(80))[%(date)s]
]
#v(0.6em)
"""


def main():
    parts = [p.read_text().strip() for p in sorted((HERE / "sections").glob("*.md"))]
    md = "\n\n".join(parts)
    marked = cite(sources(md))         # citations still as markers: latex.py turns them into \\cite
    md = numbered(marked)
    names = load_refs()
    names.update({k: v for k, v in EXTRA_NAMES.items() if k not in names})
    appendix = ["# Appendix B. Artifact index", "",
                "Bracketed tags in the text name the place in the project repository where the "
                "measurement, its raw logs and its scripts live.", "",
                "| tag | path |", "|---|---|"]
    key = lambda t: (0, int(re.sub(r"\D", "", t)), t) if re.match(r"E\d", t) else (1, 0, t)
    appendix += [f"| {t} | `{tags[t]}` |" for t in sorted(tags, key=key)]
    bib = load_bib()
    unverified = [i for i in refs if not bib.get(i, {}).get("verified")]
    reflist = ["# References", "",
               "Authors, titles and dates are as recorded by arXiv (`refs.json`, regenerated by "
               "`fetch_refs.py` from the saved API responses); a venue is given where the arXiv record "
               "or the project bibliography (`research/lowrank/paper/references.md`) states one. "
               "The project bibliography also records what each source was used for."
               + (" Entries marked (unverified) could not be matched to an arXiv record and carry "
                  "only the short name used in the project bibliography." if unverified else ""), ""]
    reflist += [ref_entry(n, i, bib, names) + "<br>" for n, i in enumerate(refs, 1)]
    full_md = f"# {TITLE}\n\n{AUTHOR} — {AFFIL} — {DATE}\n\n" + md + "\n\n" + "\n".join(appendix) + "\n\n" + "\n".join(reflist).replace("<br>", "\n") + "\n"
    full_md = full_md.replace("\x05", "[").replace("\x06", "]")
    (HERE / "paper.md").write_text(full_md)

    body = to_typst(md) + "\n" + to_typst("\n".join(appendix)) + "\n" + to_typst("\n".join(reflist).replace("<br>", "\n\n"))
    typ = PREAMBLE % dict(title=TITLE, author=AUTHOR, affil=AFFIL, date=DATE) + body
    (HERE / "paper.typ").write_text(typ)
    print(f"words: {len(md.split())}  refs: {len(refs)} ({len(unverified)} unverified)  tags: {len(tags)}")
    do_latex = "--no-latex" not in sys.argv
    if do_latex:
        tex = latex.document(marked, "\n".join(appendix), "\n".join(reflist[:3]),
                             [ref_parts(i, bib, names) for i in refs], TITLE, AUTHOR, AFFIL, DATE)
        (HERE / "paper.tex").write_text(tex)
    rc = 0
    if shutil.which("typst"):
        r = subprocess.run(["typst", "compile", "paper.typ", "paper.pdf"], cwd=HERE, capture_output=True, text=True)
        print(r.stderr[-3000:] or "paper.pdf written")
        rc = r.returncode
    else:
        print("typst not on PATH — wrote paper.md and paper.typ only")
    if do_latex:
        rc = compile_latex() or rc
    sys.exit(rc)


def find_tectonic():
    for c in (os.environ.get("TECTONIC"), shutil.which("tectonic"),
              pathlib.Path.home() / ".local/share/openbeast/tectonic/tectonic"):
        if c and pathlib.Path(c).is_file():
            return str(c)


def compile_latex():
    """paper.tex -> paper-latex.pdf. Tectonic names its output after the input,
    which would overwrite the Typst paper.pdf, so it compiles into a temp dir."""
    exe = find_tectonic()
    if not exe:
        print("tectonic not found — wrote paper.tex only (see README.md)")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([exe, "--keep-logs", "--outdir", tmp, "paper.tex"], cwd=HERE,
                           capture_output=True, text=True)
        log = pathlib.Path(tmp, "paper.log")
        log = log.read_text(errors="replace") if log.exists() else ""
        if r.returncode:
            print(r.stderr[-3000:])
            return r.returncode
        missing, over = latex.check_log(log + r.stderr)
        shutil.copy(pathlib.Path(tmp, "paper.pdf"), HERE / "paper-latex.pdf")
        if "--keep-log" in sys.argv:
            (HERE / "paper-latex.log").write_text(log)
    print(f"paper-latex.pdf written ({len(over)} overfull boxes > 1pt"
          + (": " + ", ".join(over) + " pt" if over else "") + ")")
    if missing:
        print("LaTeX: glyphs missing from the fonts — map them in latex.UNICODE:\n  " + "\n  ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    main()
