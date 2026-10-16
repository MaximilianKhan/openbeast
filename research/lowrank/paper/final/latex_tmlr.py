#!/usr/bin/env python3
"""TMLR backend for build.py: paper-tmlr.tex (anonymous submission),
paper-tmlr-preprint.tex (de-anonymized, [preprint] option) and refs.bib.

It reuses latex.py's Markdown-subset translation and differs from the
article build in five places, each written down because each is easy to get
wrong:

* Style. tmlr.sty (vendored in tmlr/; commit and checksums in tmlr/SUBMISSION.md) is loaded
  unmodified and sets fonts (T1 + Latin Modern), page geometry, headings and
  natbib author-year citations. Nothing here overrides its layout; TMLR
  desk-rejects changed formatting.
* Characters. tmlr.sty is a pdfLaTeX style (T1 fontenc), so the generated
  file is pure ASCII: every non-ASCII character of the text is written as a
  LaTeX macro (ASCII below) in one final pass. An unmapped character stops
  the build, and the compile also fails on any "Missing character" in the
  log. The same .tex therefore compiles under Tectonic/XeTeX and pdfLaTeX.
* Citations. `arXiv:ID` markers become natbib commands chosen from the
  sentence (natbib() below): a parenthesis holding only citations becomes
  one \\citep; a citation used as a noun ("the convention of arXiv:X") a
  \\citet; any other citation inside a parenthesis a \\citealp. refs.bib is
  generated from refs.json by bibtex() below; tmlr.bst formats it.
* Order. TMLR puts the appendix after the references: main text, then the
  bibliography, then Appendix A (sections/08-appendix-a.md) and Appendix B.
* Anonymization (anonymous build only). Artifact tags render as
  "[Supp. E24]" and Appendix B maps them to locations inside the
  supplementary archive (the repository prefix is stripped). SCRUB removes
  names and paths mechanically; RESIDUE lists phrases that identify the
  work only through their meaning and cannot be rewritten mechanically - the
  build prints each hit with its section file and line for the author.
"""
import json, re, unicodedata

import latex

SUPP_PREFIX = "research/lowrank/"          # repo prefix stripped from supplementary paths
REPO_URL = "https://github.com/MaximilianKhan/openbeast"

# --------------------------------------------------------------- characters
ASCII = {
    "−": r"\ensuremath{-}", "§": r"\S{}", "±": r"\ensuremath{\pm}", "×": r"\ensuremath{\times}",
    "·": r"\ensuremath{\cdot}", "→": r"\ensuremath{\rightarrow}", "←": r"\ensuremath{\leftarrow}",
    "≈": r"\ensuremath{\approx}", "≤": r"\ensuremath{\leq}", "≥": r"\ensuremath{\geq}",
    "≠": r"\ensuremath{\neq}", "≳": r"\ensuremath{\gtrsim}", "≲": r"\ensuremath{\lesssim}",
    "‖": r"\ensuremath{\|}", "∞": r"\ensuremath{\infty}", "√": r"\ensuremath{\surd}",
    "∈": r"\ensuremath{\in}", "≡": r"\ensuremath{\equiv}", "∝": r"\ensuremath{\propto}",
    "Δ": r"\ensuremath{\Delta}", "Σ": r"\ensuremath{\Sigma}", "σ": r"\ensuremath{\sigma}",
    "α": r"\ensuremath{\alpha}", "β": r"\ensuremath{\beta}", "γ": r"\ensuremath{\gamma}",
    "δ": r"\ensuremath{\delta}", "ε": r"\ensuremath{\varepsilon}", "λ": r"\ensuremath{\lambda}",
    "μ": r"\ensuremath{\mu}", "µ": r"\ensuremath{\mu}", "τ": r"\ensuremath{\tau}",
    "ρ": r"\ensuremath{\rho}", "θ": r"\ensuremath{\theta}", "π": r"\ensuremath{\pi}",
    "ᵀ": r"\textsuperscript{T}", "²": r"\textsuperscript{2}", "³": r"\textsuperscript{3}",
    "¹": r"\textsuperscript{1}", "°": r"\ensuremath{^\circ}",
    "“": "``", "”": "''", "‘": "`", "’": "'", "–": "--", "—": "---", "…": r"\ldots{}",
    "\u00a0": "~",
}
ACCENT = {"\u0301": "'", "\u0300": "`", "\u0308": '"', "\u0302": "^", "\u0303": "~",
          "\u030c": "v", "\u0327": "c", "\u0304": "=", "\u0306": "u", "\u030a": "r"}


def to_ascii(s, where):
    """Every non-ASCII character as a LaTeX macro; stop on one with no mapping."""
    out, bad = [], set()
    i = 0
    while i < len(s):
        c = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if ord(c) < 128 and nxt in ACCENT:                 # base letter + combining accent (H̃)
            out.append("\\%s{%s}" % (ACCENT[nxt], c)); i += 2; continue
        if ord(c) < 128:
            out.append(c)
        elif c in ASCII:
            out.append(ASCII[c])
        else:
            d = unicodedata.normalize("NFD", c)            # é, ç, š, ć …
            if len(d) == 2 and ord(d[0]) < 128 and d[1] in ACCENT:
                out.append("{\\%s{%s}}" % (ACCENT[d[1]], d[0]))
            else:
                bad.add(c); out.append(c)
        i += 1
    if bad:
        raise SystemExit(f"latex_tmlr.py: no ASCII mapping in {where} for "
                         + ", ".join(f"{c!r} U+{ord(c):04X}" for c in sorted(bad))
                         + " - add it to latex_tmlr.ASCII")
    return "".join(out)


# --------------------------------------------------------------- citations
_C = latex.CITE0 + r"([^" + latex.CITE1 + r"]*)" + latex.CITE1


def natbib(md):
    """Citation markers (one arXiv id each) -> natbib commands, from context."""
    def group(m):
        inner = m.group(1)
        if latex.CITE0 not in inner:
            return m.group(0)
        if not re.sub(_C + r"|[\s,;]|\band\b", "", inner):     # only citations: one \citep
            ids = re.findall(_C, inner)
            return latex.CITE0 + "citep" + latex.CITESEP + ",".join(ids) + latex.CITE1
        return "(" + mark(inner, inside=True) + ")"

    def mark(s, inside):
        def rep(m):
            before = s[:m.start()].rstrip()
            noun = bool(before) and before[-1].isalpha()      # "the convention of arXiv:X"
            cmd = "citet" if noun or not inside else "citealp"
            return latex.CITE0 + cmd + latex.CITESEP + m.group(1) + latex.CITE1
        return re.sub(_C, rep, s)

    # innermost parentheses first, then whatever is left (in outer parentheses or none)
    md = re.sub(r"\(([^()\n]*)\)", group, md)
    out, depth, last = [], 0, 0
    for m in re.finditer(r"[()]|" + _C, md):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth = max(depth - 1, 0)
        elif latex.CITESEP not in tok:
            before = md[:m.start()].rstrip()
            noun = bool(before) and before[-1].isalpha()
            cmd = "citet" if noun or depth == 0 else "citealp"
            out.append(md[last:m.start()] + latex.CITE0 + cmd + latex.CITESEP + m.group(1) + latex.CITE1)
            last = m.end()
    out.append(md[last:])
    return "".join(out)


# --------------------------------------------------------------- refs.bib
VENUES = {"ICML": "International Conference on Machine Learning (ICML)",
          "ICLR": "International Conference on Learning Representations (ICLR)",
          "NeurIPS": "Advances in Neural Information Processing Systems (NeurIPS)",
          "AAAI": "AAAI Conference on Artificial Intelligence (AAAI)",
          "COLM": "Conference on Language Modeling (COLM)",
          "ICLR Workshop": "ICLR Workshop"}
# Journal references are free text in the arXiv record; their fields are
# written out here and the build stops if the record's text changes.
JOURNALS = {
    "1012.0197": ("SIAM J. Matrix Anal. & Appl. 32 (4), pp. 1149-1165, 2011",
                  dict(journal="SIAM Journal on Matrix Analysis and Applications", volume="32",
                       number="4", pages="1149--1165", year="2011")),
    "0909.4061": ("SIAM Rev., Survey and Review section, Vol. 53, num. 2, pp. 217-288, June 2011",
                  dict(journal="SIAM Review", volume="53", number="2", pages="217--288", year="2011")),
    "2501.18475": ("Transactions on Machine Learning Research (TMLR), 2025",
                   dict(journal="Transactions on Machine Learning Research", year="2025")),
}
PARTICLES = {"De", "El", "Van", "Von", "Da", "Di", "Le", "La", "Del", "Der"}


def bib_name(name):
    """'Christopher De Sa' -> 'De Sa, Christopher' (bibtex would take 'Sa' as the last name)."""
    p = name.split()
    if len(p) >= 3 and p[-2] in PARTICLES:
        return f"{p[-2]} {p[-1]}, {' '.join(p[:-2])}"
    return name


def bib_text(s, where):
    s = "".join(latex.SPECIAL.get(c, c) if c in "&%$#_" else c for c in s)
    return to_ascii(s, where)


def bibtex(entries, withheld):
    """refs.json entries -> refs.bib text. Keys are latex.bibkey(id), as in the article build."""
    out = ["% Generated by build.py (latex_tmlr.py) from refs.json - do not edit.",
           "% Every entry keeps its arXiv id (eprint). Entries with a venue print it;",
           "% the rest print as arXiv preprints. See tmlr/SUBMISSION.md for the three",
           "% whose venue is withheld pending a proceedings check.", ""]
    for e in entries:
        i = e["id"]
        if not e.get("verified"):
            raise SystemExit(f"latex_tmlr.py: refs.json entry {i} is not verified; fix it before a TMLR build")
        where = f"refs.json {i}"
        f = {"author": " and ".join(bib_text(bib_name(a), where) for a in e["authors"]),
             "title": "{" + bib_text(e["title"].rstrip("."), where) + "}"}
        link = r"\href{https://arxiv.org/abs/%s}{arXiv:%s}" % (i, i)
        v = e.get("venue")
        if i in JOURNALS:
            expect, fields = JOURNALS[i]
            if v != expect:
                raise SystemExit(f"latex_tmlr.py: journal reference of {i} changed ({v!r}); update JOURNALS")
            kind = "article"; f.update(fields); f["note"] = link
        elif v and e.get("venue_year"):
            if v not in VENUES:
                raise SystemExit(f"latex_tmlr.py: venue {v!r} of {i} has no entry in VENUES")
            kind = "inproceedings"; f["booktitle"] = VENUES[v]; f["year"] = str(e["venue_year"])
            f["note"] = link
        elif v:
            raise SystemExit(f"latex_tmlr.py: venue {v!r} of {i} has no year and no JOURNALS entry")
        else:
            kind = "article"; f["journal"] = "arXiv preprint " + link; f["year"] = str(e["year"])
        f["eprint"] = i; f["archiveprefix"] = "arXiv"
        if i in withheld:
            out.append("% venue withheld (refs/review.json): " + to_ascii(withheld[i], where))
        out.append("@%s{%s,\n%s\n}\n" % (kind, latex.bibkey(i),
                                         ",\n".join(f"  {k} = {{{val}}}" for k, val in f.items())))
    return "\n".join(out)


# --------------------------------------------------------------- anonymity
# Mechanical scrubs of the Markdown, anonymous build only: (pattern, replacement).
SCRUB = [
    (r"`" + re.escape(SUPP_PREFIX) + r"([^`]*)`", r"`\1`"),       # repo paths -> archive paths
    (re.escape(SUPP_PREFIX), ""),
    (r"https?://github\.com/[\w./-]+", "[URL withheld]"),
    (r"github\.com/[\w./-]+", "[URL withheld]"),
    (r"/home/[\w./-]+", "[path withheld]"),
    (r"(?i)openbeast", "[withheld]"),
    (r"(?i)beast-?rank", "[withheld]"),
    (r"\bMaximilian\b|\bMax Khan\b|\bKhan\b", "[withheld]"),
]
# Strings that must not survive into the anonymous PDF (checked on its text).
FORBIDDEN = [r"(?i)openbeast", r"(?i)beast", r"(?i)maximilian", r"\bMax\b", r"(?i)\bkhan\b",
             r"(?i)github\s*\.\s*com", r"research/lowrank", r"/home/", r"maxjkh", r"passboards"]
# Phrases that point at the author's own (unpublished, public) project by meaning.
RESIDUE = [
    (r"(?i)\b(?:the |our )?project(?:'s)? (?:repository|journal|theory note|review notes|own|bibliography)",
     "names the project's own record - say 'supplementary material' or keep if the archive ships it"),
    (r"(?i)\bour journal\b", "self-reference to an internal record"),
    (r"(?i)in the repository\b", "a repository the reviewers cannot see"),
    (r"(?i)\bbuild [0-9a-f]{7,}\b", "a commit hash of the author's local llama.cpp tree (searchable if pushed)"),
    (r"(?i)\b(?:internal|second internal) review", "internal process; fine if the review notes ship anonymized"),
]


def scrub(md):
    for pat, rep in SCRUB:
        md = re.sub(pat, rep, md)
    return md


def residue(section_files):
    """[(file, line, phrase, why)] for RESIDUE hits in the source sections."""
    hits = []
    for p in section_files:
        for n, ln in enumerate(p.read_text().splitlines(), 1):
            text = re.sub(r"\[source:[^\]]*\]", "", ln)
            for pat, why in RESIDUE:
                for m in re.finditer(pat, text):
                    hits.append((p.name, n, m.group(0), why))
    return hits


# --------------------------------------------------------------- document
PREAMBLE = r"""%% Generated by build.py (latex_tmlr.py) from sections/*.md - do not edit; edit the sections and rebuild.
%% %(what)s
%% Pure ASCII; compiles with pdflatex+bibtex or Tectonic. Needs tmlr.sty and tmlr.bst
%% (tmlr/), refs.bib and figures/ next to it: see README.md "The TMLR build".
\documentclass[10pt]{article}
\usepackage%(option)s{tmlr}
\usepackage{amsmath,amssymb}
\usepackage{microtype}
\usepackage{graphicx,xcolor}
\usepackage{booktabs,array,tabularx,longtable}
\usepackage{hyperref}
\usepackage{url}
\hypersetup{colorlinks=true,linkcolor=blue!50!black,citecolor=blue!50!black,urlcolor=blue!50!black,
  pdftitle={%(pdftitle)s},pdfauthor={%(pdfauthor)s}}
\setlength{\emergencystretch}{2em}
\renewcommand{\arraystretch}{1.12}
\newcommand{\src}[1]{{\footnotesize\color{black!57}\ [%(srcprefix)s#1]}}
\title{%(title)s}
\author{%(author)s}
\begin{document}
\maketitle
"""


def document(marked, tags, mode, title, author_name, author_block, first_page_note=None):
    """mode 'anon' or 'preprint'. `marked` is build.py's Markdown with source and
    citation markers in place; `tags` maps tag -> repo path. `first_page_note`
    (Markdown, optional) becomes a first-page footnote on the title: TMLR asks
    for the LLM-use disclosure there."""
    anon = mode == "anon"
    md = scrub(marked) if anon else marked
    md = natbib(md)
    head, sep, appx = md.partition("\n# Appendix A")
    if not sep:
        raise SystemExit("latex_tmlr.py: no '# Appendix A' heading to split the appendix at")
    appx = "# Appendix A" + appx
    key = lambda t: (0, int(re.sub(r"\D", "", t)), t) if re.match(r"E\d", t) else (1, 0, t)
    if anon:
        b = ["# Appendix B. Supplementary material index", "",
             "Bracketed tags in the text (for example [Supp. E24]) name items of the supplementary "
             "material: the directory or file in the supplementary archive that holds the measurement, "
             "its raw logs and its scripts.", "", "| item | location in the supplementary archive |", "|---|---|"]
        b += [f"| {t} | `{tags[t].removeprefix(SUPP_PREFIX)}` |" for t in sorted(tags, key=key)]
    else:
        b = ["# Appendix B. Artifact index", "",
             f"Bracketed tags in the text name the place in the public repository [{REPO_URL.removeprefix('https://')}]"
             f"({REPO_URL}) where the measurement, its raw logs and its scripts live.", "",
             "| tag | path |", "|---|---|"]
        b += [f"| {t} | `{tags[t]}` |" for t in sorted(tags, key=key)]

    saved = (latex.TEXTWIDTH_PT, latex.TABLE_FONT, latex.CHAR_PT)
    # tmlr.sty: \textwidth 6.5in; tables at \small (9pt Latin Modern: 4.30pt per estimated character, measured on the §5.2 table; 4.4 leaves margin)
    latex.TEXTWIDTH_PT, latex.TABLE_FONT, latex.CHAR_PT = 469.75, r"\small", 4.4
    try:
        latex.labels.clear(); latex.dangling.clear()
        latex.scan_headings(md)
        abstract, body = latex.to_latex(head)
        _, appendix_a = latex.to_latex(appx)
        _, appendix_b = latex.to_latex("\n".join(b))
    finally:
        latex.TEXTWIDTH_PT, latex.TABLE_FONT, latex.CHAR_PT = saved
    if latex.dangling:
        raise SystemExit("latex_tmlr.py: §-references with no matching heading: " + ", ".join(sorted(set(latex.dangling))))
    tex = PREAMBLE % dict(
        what=("ANONYMOUS SUBMISSION - \\usepackage{tmlr}, no author names." if anon else
              "PREPRINT - \\usepackage[preprint]{tmlr}, de-anonymized."),
        option="" if anon else "[preprint]", pdftitle=latex.esc(title),
        title=latex.esc(title) + (r"\thanks{%s}" % latex.inline(first_page_note) if first_page_note else ""),
        pdfauthor="" if anon else latex.esc(author_name),
        author=author_block if not anon else "Anonymous authors",
        srcprefix=r"Supp.\ " if anon else "")
    if abstract:
        tex += "\\begin{abstract}\n" + abstract + "\\end{abstract}\n\n"
    tex += body + "\n\\label{tmlr-main-end}\n\\bibliography{refs}\n\\bibliographystyle{tmlr}\n\n\\clearpage\n\\appendix\n\\label{tmlr-appendix}\n" \
        + appendix_a + "\n" + appendix_b + "\n\\end{document}\n"
    pre, sep, post = tex.partition("\\begin{document}")
    post = re.sub(r"-(?=-)", "-{}", post)       # T1 fonts ligate -- and ---; the text means hyphens
    post = post.replace("<<", "<{}<").replace(">>", ">{}>")
    return to_ascii(pre + sep + post, "paper-tmlr")
