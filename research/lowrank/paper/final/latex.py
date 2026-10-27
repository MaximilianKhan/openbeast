#!/usr/bin/env python3
"""LaTeX backend for build.py: the same Markdown subset, written as paper.tex.

build.py hands this module the assembled Markdown with three kinds of marker
left in place (artifact tags, citations, nothing else) and the structured
reference list; `document()` returns one self-contained .tex file.

Decisions that are easy to get wrong and are therefore written down:

* Engine. The text is full of Unicode (−, ×, ≈, σ, Δ, →, ᵀ, ‖, H̃ …) and is
  passed through as UTF-8, so the file needs XeLaTeX / LuaLaTeX / Tectonic and
  fontspec. Fonts are Libertinus Serif (the Typst build's face) and DejaVu
  Sans Mono, both in TeX Live and in Tectonic's bundle, loaded by file name.
  The handful of characters those fonts lack are mapped in UNICODE below;
  `check_log()` fails the build on any "Missing character" line.
* Section numbers. The headings carry manual numbers ("4.3b", "4.6b") that
  LaTeX's counters cannot reproduce, so headings are starred and keep the
  number as written; each gets a label `sec:<number>` and every "§x.y" in the
  text becomes a \\hyperref to it. `document()` refuses a §-reference that
  has no heading.
* TeX ligatures are switched off for the text font, so "--" stays two hyphens
  as in the Typst build, and quotes are curled here instead.
* Tables are never wider than the text: columns that fit get `l`, the rest
  share what is left as ragged tabularx `X` columns, weighted by content.
"""
import re

# Characters the two fonts do not carry, as (text-mode replacement).
UNICODE = {
    "ᵀ": r"\textsuperscript{T}",
    "≳": r"\ensuremath{\gtrsim}",
}
SPECIAL = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "#": r"\#", "%": r"\%",
           "&": r"\&", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
SRC0, SRC1, CITE0, CITE1 = "\x05", "\x06", "\x0e", "\x0f"

labels = set()          # heading numbers that exist ("4", "4.3b", …)
dangling = []           # §-references with no heading


def esc(s):
    return "".join(SPECIAL.get(c) or UNICODE.get(c) or c for c in s)


def quotes(s):
    """Curl straight quotes the way Typst's smartquote does."""
    def rep(m):
        before = s[m.start() - 1] if m.start() else " "
        opening = before.isspace() or before in "([{—–/\x01\x03"
        return ("“" if opening else "”") if m.group() == '"' else ("‘" if opening else "’")
    return re.sub(r"[\"']", rep, s)


def code(s, breakable=True):
    out = esc(s)
    if breakable:                                   # long paths must be able to wrap
        out = re.sub(r"(/|\\_)(?=.)", r"\1\\allowbreak{}", out)
    return r"\texttt{" + out + "}"


def secref(m):
    n = m.group(1)
    if n not in labels:
        dangling.append(n)
        return m.group(0)
    return r"\hyperref[sec:%s]{§%s}" % (n, n)


def inline(s, breakable=True):
    codes = []
    s = re.sub(r"`([^`]*)`", lambda m: codes.append(m.group(1)) or f"\x07{len(codes) - 1}\x07", s)
    links = []
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: links.append(m.groups()) or f"\x10{len(links) - 1}\x10", s)
    s = re.sub(r"\*\*(.+?)\*\*", "\x01\\1\x02", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", "\x03\\1\x04", s)
    s = re.sub(r"\^\{([^}]*)\}", "\x08\\1\x09", s)
    s = re.sub(r"\^([−-]?[\d.]*\d)", "\x08\\1\x09", s)
    s = s.replace("\\|", "|")
    s = esc(quotes(s))
    s = re.sub(r"§(\d+(?:\.\d+[a-z]?)?)", secref, s)
    s = re.sub(CITE0 + "([^" + CITE1 + "]*)" + CITE1, lambda m: r"\cite{%s}" % bibkey(m.group(1)), s)
    for a, b in (("\x01", r"\textbf{"), ("\x02", "}"), ("\x03", r"\emph{"), ("\x04", "}"),
                 (SRC0, r"\src{"), (SRC1, "}"), ("\x08", r"\textsuperscript{"), ("\x09", "}")):
        s = s.replace(a, b)
    s = re.sub(r"\x10(\d+)\x10", lambda m: r"\href{%s}{%s}" % (
        links[int(m.group(1))][1].replace("%", r"\%").replace("#", r"\#"),
        esc(links[int(m.group(1))][0])), s)
    return re.sub(r"\x07(\d+)\x07", lambda m: code(codes[int(m.group(1))], breakable), s)


def bibkey(arxiv_id):
    return "arxiv" + arxiv_id


def cells(row):
    return [c.strip() for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))]


# ------------------------------------------------------------------- tables
TEXTWIDTH_PT = 462.5        # 8.5in - 2 x 1.05in
TABLE_FONT = r"\fontsize{8.5}{10.5}\selectfont"      # the Typst build's table size
CHAR_PT = 3.5               # mean advance of Libertinus Serif at 8.5pt, measured on set tables
COLSEP_PT = 4.0


def width(cell):
    """Estimated set width of a cell in characters of the text font."""
    mono = sum(len(m) for m in re.findall(r"`([^`]*)`", cell))
    plain = re.sub(r"[*`]|\\(?=\|)|[\x05\x06\x0e\x0f]", "", cell)
    plain = re.sub(r"\x0e[^\x0f]*\x0f", "[00]", plain)
    digits = sum(ch.isdigit() or ch in "−+±=" for ch in plain)      # wider than the mean letter
    return len(plain) + 0.15 * digits + 0.3 * mono + (0.08 * len(plain) if "**" in cell else 0)


def guard(c):
    """A cell that opens with [ or * would be read as an argument of the preceding \\\\."""
    return "{}" + c if c[:1] in "[*" else c


def table(head, rows):
    n = len(head)
    # In a table `\|` is the only way to write a bar, inside a code span too.
    head = [c.replace("\\|", "|") for c in head]
    rows = [[c.replace("\\|", "|") for c in (r + [""] * n)[:n]] for r in rows]
    nat = [max(width(r[i]) for r in [head] + rows) + 1 for i in range(n)]
    budget = (TEXTWIDTH_PT - 2 * COLSEP_PT * (n - 1)) / CHAR_PT
    wide = [False] * n
    if sum(nat) > budget:
        # Give every column that fits its fair share an `l`; the rest wrap.
        left, k = budget, n
        fixed = [False] * n
        while True:
            share = left / max(k, 1)
            hit = [i for i in range(n) if not fixed[i] and nat[i] <= share]
            if not hit:
                break
            for i in hit:
                fixed[i] = True; left -= nat[i]; k -= 1
        wide = [not f for f in fixed]
    long = len(rows) > 45          # taller than a page: longtable; shorter tables stay in one piece
    if any(wide):
        tot = sum(nat[i] for i in range(n) if wide[i])
        k = sum(wide)
        if long:        # longtable has no X column: fix every width from the estimate
            spec = "".join(r">{\raggedright\arraybackslash}p{%.3f\linewidth}" % (
                (left * nat[i] / tot if wide[i] else nat[i]) * CHAR_PT / TEXTWIDTH_PT) for i in range(n))
        else:
            spec = "".join(r">{\hsize=%.3f\hsize\raggedright\arraybackslash}X" % (k * nat[i] / tot)
                           if wide[i] else "l" for i in range(n))
    else:
        spec = "l" * n
    spec = "@{}" + spec + "@{}"
    fmt = lambda r, bold=False: " & ".join(
        guard((r"\textbf{%s}" % inline(c, wide[i])) if bold and c else inline(c, wide[i]))
        for i, c in enumerate(r)) + r" \\"
    body = [fmt(r) for r in rows]
    if long:
        return "\n".join([r"\begingroup" + TABLE_FONT + r"\setlength{\tabcolsep}{%gpt}" % COLSEP_PT,
                          r"\begin{longtable}{%s}" % spec, r"\toprule", fmt(head, True), r"\midrule",
                          r"\endhead", r"\bottomrule", r"\endfoot", *body,
                          r"\end{longtable}", r"\endgroup", ""])
    env = (r"\begin{tabularx}{\linewidth}{%s}" % spec, r"\end{tabularx}") if any(wide) else \
          (r"\begin{tabular}{%s}" % spec, r"\end{tabular}")
    return "\n".join([r"\begin{center}" + TABLE_FONT + r"\setlength{\tabcolsep}{%gpt}" % COLSEP_PT, env[0],
                      r"\toprule", fmt(head, True), r"\midrule", *body, r"\bottomrule", env[1],
                      r"\end{center}", ""])


# ------------------------------------------------------------------- blocks
def split_heading(text):
    """'4.3c Trained versus …' -> ('4.3c', 'Trained versus …'); unnumbered -> (None, text)."""
    m = re.match(r"(\d+(?:\.\d+[a-z]?)?)\.?\s+(.*)", text)
    return (m.group(1), m.group(2)) if m else (None, text)


def scan_headings(md):
    for ln in md.split("\n"):
        if ln.startswith("#"):
            num, _ = split_heading(ln.lstrip("#").strip())
            if num:
                if num in labels:
                    raise SystemExit(f"latex.py: heading number {num} appears twice")
                labels.add(num)


def heading(level, text):
    cmd = {1: "section", 2: "subsection", 3: "subsubsection"}[min(level, 3)]
    num, title = split_heading(text)
    plain = re.sub(r"[*`]", "", text)
    if num:
        return (r"\%s*{\phantomsection\label{sec:%s}%s\hspace{0.8em}%s}" % (cmd, num, num, inline(title))
                + "\n" + r"\addcontentsline{toc}{%s}{%s}" % (cmd, esc(plain)) + "\n")
    return (r"\%s*{\phantomsection %s}" % (cmd, inline(title)) + "\n"
            + r"\addcontentsline{toc}{%s}{%s}" % (cmd, esc(plain)) + "\n")


def to_latex(md):
    """Returns (abstract, body). The abstract is the text under '## Abstract'."""
    out, abstract, lines, i = [], [], md.split("\n"), 0
    sink = out
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("#"):
            level = len(ln) - len(ln.lstrip("#"))
            text = ln[level:].strip()
            if text.lower() == "abstract":
                sink = abstract
            else:
                sink = out
                sink.append(heading(level, text))
            i += 1
        elif ln.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s:|-]+\|$", lines[i + 1].strip()):
            head, rows = cells(ln), []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cells(lines[i])); i += 1
            sink.append(table(head, rows))
        elif ln.startswith("!["):
            m = re.match(r"!\[(.*)\]\((.*)\)", ln)
            path = re.sub(r"\.svg$", ".pdf", m.group(2))      # LaTeX cannot include SVG
            cap = inline(re.sub(r"^Figure \d+\.\s*", "", m.group(1)))
            sink.append("\\begin{figure}[!htbp]\\centering\n\\includegraphics[width=0.88\\linewidth]{%s}\n"
                        "\\small\\caption{%s}\n\\end{figure}\n" % (path, cap))
            i += 1
        elif ln.startswith("- "):
            sink.append(r"\begin{itemize}")
            while i < len(lines) and lines[i].startswith("- "):
                sink.append(r"\item\relax " + inline(lines[i][2:])); i += 1
            sink.append(r"\end{itemize}" + "\n")
        else:
            sink.append(inline(ln) + "\n")
            i += 1
    return "\n".join(abstract), "\n".join(out)


PREAMBLE = r"""%% Generated by build.py (latex.py) from sections/*.md - do not edit; edit the sections and rebuild.
%% Needs a Unicode engine: tectonic paper.tex, or xelatex/lualatex (twice, for the cross-references).
\documentclass[10pt,letterpaper]{article}
\usepackage[letterpaper,hmargin=1.05in,vmargin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{fontspec}
\defaultfontfeatures[\rmfamily,\sffamily]{}%% no TeX ligatures: "--" is two hyphens, quotes are already curled
\setmainfont{LibertinusSerif}[Extension=.otf,UprightFont=*-Regular,BoldFont=*-Bold,
  ItalicFont=*-Italic,BoldItalicFont=*-BoldItalic]
\setmonofont{DejaVuSansMono}[Extension=.ttf,UprightFont=*,BoldFont=*-Bold,
  ItalicFont=*-Oblique,BoldItalicFont=*-BoldOblique,Scale=0.78]
\usepackage{microtype}
\usepackage{graphicx,xcolor}
\usepackage{booktabs,array,tabularx,longtable}
\usepackage{enumitem}
\usepackage[colorlinks=true,linkcolor=black,citecolor=black,urlcolor=blue!45!black,
  pdftitle={%(pdftitle)s},pdfauthor={%(pdfauthor)s}]{hyperref}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.62em plus 0.1em minus 0.05em}
\setlist[itemize]{leftmargin=1.5em,itemsep=0.25em,topsep=0.1em,parsep=0pt}
\setlength{\emergencystretch}{2em}
\renewcommand{\arraystretch}{1.12}
\makeatletter
\renewcommand\section{\@startsection{section}{1}{\z@}{-1.6em plus -.3em}{0.5em}{\normalfont\fontsize{13}{15.5}\selectfont\bfseries\raggedright}}
\renewcommand\subsection{\@startsection{subsection}{2}{\z@}{-1.1em plus -.2em}{0.3em}{\normalfont\fontsize{11}{13.5}\selectfont\bfseries\raggedright}}
\makeatother
\newcommand{\src}[1]{{\fontsize{7.5}{9}\selectfont\color{black!57}\ [#1]}}
\title{\fontsize{15}{18.5}\selectfont\bfseries %(title)s}
\author{\normalsize\fontsize{11}{14}\selectfont %(author)s\\[0.3em]
  \footnotesize\color{black!69}%(affil)s}
\date{\footnotesize\color{black!69}%(date)s}
\begin{document}
\maketitle
"""


def affiliation(s):
    """'OpenBeast project · github.com/x/y' - link the host/path part."""
    return re.sub(r"\b(github\.com/[\w./-]+)", lambda m: r"\href{https://%s}{%s}" % (m.group(1), esc(m.group(1))),
                  s) if "\\" not in s and not re.search(r"[{}$#%&_~^]", s) else esc(s)


def reference(parts):
    """parts = (key id, text before the id, arXiv id, text after it)."""
    i, before, after = parts
    return (r"\bibitem{%s} %s\href{https://arxiv.org/abs/%s}{arXiv:%s}%s"
            % (bibkey(i), esc(quotes(before)), i, i, esc(after)))


def document(md, appendix_md, ref_intro_md, ref_parts, title, author, affil, date):
    labels.clear(); dangling.clear()
    scan_headings(md)
    abstract, body = to_latex(md)
    _, appendix = to_latex(appendix_md)
    _, ref_intro = to_latex(ref_intro_md)
    if dangling:
        raise SystemExit("latex.py: §-references with no matching heading: " + ", ".join(sorted(set(dangling))))
    tex = PREAMBLE % dict(title=esc(title), author=esc(author), affil=affiliation(affil), date=esc(date),
                          pdftitle=esc(title), pdfauthor=esc(author))
    if abstract:
        tex += "\\begin{abstract}\n\\noindent " + abstract + "\\end{abstract}\n\n"
    tex += body + "\n" + appendix + "\n" + ref_intro
    # thebibliography would print its own heading; ours is already there with its note.
    tex += ("\\begingroup\\renewcommand{\\section}[2]{}\\small\n\\begin{thebibliography}{%d}\n"
            "\\setlength{\\itemsep}{0.15em}\n" % max(len(ref_parts), 1))
    tex += "\n".join(reference(p) for p in ref_parts)
    tex += "\n\\end{thebibliography}\n\\endgroup\n\\end{document}\n"
    return tex


def check_log(log):
    """(missing-character lines, overfull-hbox lines wider than 1pt)."""
    missing = sorted(set(re.findall(r"Missing character:[^\n]*", log)))
    over = [m for m in re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide\)[^\n]*", log) if float(m) > 1.0]
    return missing, over
