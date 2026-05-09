#!/usr/bin/env python3
"""Regenerate the leaderboard tables in README.md and docs/RESULTS.md from
leaderboard.json once the v3.5 sweep finishes (Gemma row replaces _running_).

Idempotent — running this twice produces the same output. Designed to be
called from `scripts/wrap_up.sh` after `scoring.py --rebuild`.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEADERBOARD = REPO / "evals" / "leaderboard.json"
README = REPO / "README.md"
RESULTS_MD = REPO / "docs" / "RESULTS.md"

# Sonnet 4.6 pricing — sense-of-scale baseline, no caching
USD_PER_M_INPUT = 3.0
USD_PER_M_OUTPUT = 15.0


def fmt_hms(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m:02d}m"


def cost_estimate(prompt: int, completion: int) -> float:
    return (prompt * USD_PER_M_INPUT + completion * USD_PER_M_OUTPUT) / 1_000_000


def fmt_tokens(n: int) -> str:
    return f"{n / 1e6:.2f} M"


def gen_headline_leaderboard(entries: list[dict]) -> str:
    """README.md / RESULTS.md headline table — accuracy-first, no composite."""
    rows = sorted([e for e in entries if e.get("tasks_total") == 323],
                  key=lambda x: -x["accuracy"])
    lines = [
        "| # | Model | Acc | Speed | Pass | Hard | Tokens | Cost ≈ | Wall |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, e in enumerate(rows, 1):
        is_top = (i == 1)
        model = f"**{e['model']}**" if is_top else e["model"]
        acc = f"**{e['accuracy']:.2f}**" if is_top else f"{e['accuracy']:.2f}"
        passed = f"**{e['tasks_passed']}/{e['tasks_total']}**" if is_top else f"{e['tasks_passed']}/{e['tasks_total']}"
        hard_pass = f"**{e['tasks_hard_passed']}/120**" if is_top else f"{e['tasks_hard_passed']}/120"
        tokens = fmt_tokens(e["tokens_total"])
        cost = f"${cost_estimate(e['tokens_prompt'], e['tokens_completion']):.2f}"
        wall = fmt_hms(e["elapsed_total_seconds"])
        lines.append(f"| {i} | {model} | {acc} | {e['speed']:.2f} | {passed} | {hard_pass} | {tokens} | {cost} | {wall} |")
    return "\n".join(lines)


def gen_lang_glance(entries: list[dict]) -> str:
    """Per-language at-a-glance table for README.md.
    Bold = top score in column, italic = floor."""
    rows = sorted([e for e in entries if e.get("tasks_total") == 323],
                  key=lambda x: -x["accuracy"])
    langs = ["python", "c", "cpp", "go", "rust", "zig"]
    lang_names = {"python": "Python", "c": "C", "cpp": "C++", "go": "Go", "rust": "Rust", "zig": "Zig"}
    # find top + floor per language
    col_vals = {l: [e.get("by_language", {}).get(l, {}).get("accuracy") for e in rows] for l in langs}
    top = {l: max(v for v in col_vals[l] if v is not None) for l in langs if any(v is not None for v in col_vals[l])}
    floor = {l: min(v for v in col_vals[l] if v is not None) for l in langs if any(v is not None for v in col_vals[l])}

    header = "| Model | " + " | ".join(lang_names[l] for l in langs) + " | Best at |"
    sep = "|---|" + "---:|" * len(langs) + "---|"
    out_lines = [header, sep]
    for e in rows:
        name = e["model"]
        cells = []
        best_langs = []
        for l in langs:
            v = e.get("by_language", {}).get(l, {}).get("accuracy")
            if v is None:
                cells.append("—")
                continue
            cell = f"{v:.1f}"
            if v == top.get(l):
                cell = f"**{cell}**"
                best_langs.append(lang_names[l])
            elif v == floor.get(l):
                cell = f"_{cell}_"
            cells.append(cell)
        best_at = ", ".join(best_langs) if best_langs else "—"
        out_lines.append(f"| {name} | " + " | ".join(cells) + f" | {best_at} |")
    return "\n".join(out_lines)


def gen_sweep_totals(entries: list[dict]) -> str:
    """One-line summary of cumulative tokens + cost + wall-clock."""
    rows = [e for e in entries if e.get("tasks_total") == 323]
    if not rows:
        return ""
    n = len(rows)
    p = sum(e["tokens_prompt"] for e in rows)
    c = sum(e["tokens_completion"] for e in rows)
    wall = sum(e["elapsed_total_seconds"] for e in rows)
    return (f"**Sweep total** ({n} models): {p / 1e6:.2f} M prompt + {c / 1e6:.2f} M completion = "
            f"**{(p + c) / 1e6:.2f} M tokens**, ≈ **${cost_estimate(p, c):.2f}** API-equivalent, "
            f"**{fmt_hms(wall)}** GPU wall-time.")


def replace_block(text: str, anchor_pattern: str, new_block: str) -> tuple[str, bool]:
    """Replace the markdown table whose header matches anchor_pattern.
    Replacement extends from the matched line through the next blank line."""
    lines = text.splitlines(keepends=False)
    out = []
    i = 0
    replaced = False
    while i < len(lines):
        if not replaced and re.match(anchor_pattern, lines[i]):
            out.append(new_block)
            i += 1
            while i < len(lines) and lines[i].strip() != "":
                i += 1
            replaced = True
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), replaced


def replace_running_row(text: str) -> tuple[str, bool]:
    """Drop the `| — | Gemma 4 31B-it Q5_K_XL | _running_ ...` row if still present."""
    pattern = r"^\| — \| Gemma 4 31B-it Q5_K_XL \| _running_ \|.*$\n?"
    new_text = re.sub(pattern, "", text, count=1, flags=re.MULTILINE)
    return new_text, new_text != text


def replace_sweep_totals(text: str, totals: str) -> tuple[str, bool]:
    """Replace the existing 'Sweep total so far' / 'Sweep total' line."""
    pattern = r"^\*\*Sweep total[^\n]*$"
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, totals, text, count=1, flags=re.MULTILINE), True
    return text, False


def main() -> int:
    import sys
    force = "--force" in sys.argv
    lb = json.loads(LEADERBOARD.read_text())
    entries = lb["entries"]

    n_v35 = sum(1 for e in entries if e.get("tasks_total") == 323)
    print(f"Found {n_v35} v3.5 leaderboard entries (323-task suite).")
    if n_v35 < 5 and not force:
        print(f"ABORT: only {n_v35} v3.5 entries — Gemma run not yet reflected in leaderboard.")
        print("       Pass --force to override (will produce an incomplete table).")
        return 1
    has_gemma_v35 = any(e.get("tasks_total") == 323 and "Gemma" in e.get("model", "") for e in entries)
    if not has_gemma_v35 and not force:
        print("ABORT: no Gemma v3.5 entry in leaderboard — wait for the run to finish + scoring.py --rebuild.")
        return 1

    headline = gen_headline_leaderboard(entries)
    glance = gen_lang_glance(entries)
    totals = gen_sweep_totals(entries)

    for path in [README, RESULTS_MD]:
        text = path.read_text()
        orig = text
        text, dropped = replace_running_row(text)
        text, lb_ok = replace_block(text, r"^\| # \| Model \| Acc \| Speed \|", headline)
        if path == README:
            text, gl_ok = replace_block(text, r"^\| Model \| Python \| C \| C\+\+ \|", glance)
        else:
            gl_ok = True
        text, totals_ok = replace_sweep_totals(text, totals)
        if text != orig:
            path.write_text(text)
            print(f"  {path.relative_to(REPO)}: leaderboard={'✓' if lb_ok else '–'}  "
                  f"glance={'✓' if gl_ok else '–'}  totals={'✓' if totals_ok else '–'}  "
                  f"running_row={'dropped' if dropped else 'absent'}")
        else:
            print(f"  {path.relative_to(REPO)}: no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
