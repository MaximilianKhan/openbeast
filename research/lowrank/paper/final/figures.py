#!/usr/bin/env python3
"""Figures for the paper. Every number is copied from a results file named in
the comment beside it; nothing is computed here."""
import pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = pathlib.Path(__file__).parent / "figures"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
C1, C2, C3 = "#2a78d6", "#eb5e28", "#1b9e77"
plt.rcParams.update({"font.size": 8.5, "font.family": "DejaVu Sans", "axes.edgecolor": MUTED,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
                     "axes.axisbelow": True, "svg.fonttype": "none"})

def save(fig, name, ax=None, pdf_ylabel=None, rect=None):
    """SVG for the Typst build, PDF for the LaTeX one (LaTeX cannot include SVG).

    The SVG keeps its text as text and Typst sets it in the document font, where the long
    y label fits the axis. The PDF embeds DejaVu Sans, in which the same label is longer
    than the axis and gets cut at the figure edge, so the PDF breaks it over two lines
    (`pdf_ylabel`, same words). The PDF carries no creation date, so an unchanged figure
    is an unchanged file."""
    fig.savefig(OUT / f"{name}.svg")
    if pdf_ylabel:
        ax.set_ylabel(pdf_ylabel)
        fig.tight_layout(rect=rect)
    fig.savefig(OUT / f"{name}.pdf", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)

def fig_ladder():
    # experiments/36-submission/results-a.txt (b10865; GB = file bytes, adapter included)
    data = {  # corpus -> name -> (GB, KLD, sem)
        "wikitext (calibration)": {"IQ3_XS": (12.26, 0.0766, 0.0022), "Q3_K_S": (12.37, 0.0910, 0.0022),
            "Q3_K_M": (13.59, 0.0605, 0.0016), "control": (12.50, 0.0813, 0.0022),
            "mixed base": (12.16, 0.0971, 0.0025), "corrected": (12.50, 0.0863, 0.0023)},
        "FineWeb-Edu (held out)": {"IQ3_XS": (12.26, 0.0517, 0.0010), "Q3_K_S": (12.37, 0.0638, 0.0012),
            "Q3_K_M": (13.59, 0.0410, 0.0008), "control": (12.50, 0.0577, 0.0011),
            "mixed base": (12.16, 0.0731, 0.0014), "corrected": (12.50, 0.0686, 0.0014)},
        "code (held out)": {"IQ3_XS": (12.26, 0.0632, 0.0016), "Q3_K_S": (12.37, 0.0746, 0.0020),
            "Q3_K_M": (13.59, 0.0500, 0.0015), "control": (12.50, 0.0666, 0.0018),
            "mixed base": (12.16, 0.0830, 0.0021), "corrected": (12.50, 0.0779, 0.0020)},
    }
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 3.1), sharey=False)
    for ax, (title, d) in zip(axes, data.items()):
        rungs = [d[k] for k in ("IQ3_XS", "Q3_K_S", "Q3_K_M")]
        ax.errorbar([r[0] for r in rungs], [r[1] for r in rungs], yerr=[r[2] for r in rungs], fmt="o",
                    ms=5, color=C1, ecolor=C1, elinewidth=1, capsize=2, label="stock type (bare)")
        c = d["control"]
        ax.errorbar([c[0]], [c[1]], yerr=[c[2]], fmt="s", ms=5, color=C2, ecolor=C2, elinewidth=1,
                    capsize=2, label="same-byte mixed-type control")
        o = [d["mixed base"], d["corrected"]]
        ax.errorbar([x[0] for x in o], [x[1] for x in o], yerr=[x[2] for x in o], fmt="^", ms=6,
                    color=C3, ecolor=C3, elinewidth=1, capsize=2, label="mixed base, without / with correction")
        ax.annotate("", xy=(o[1][0] - 0.01, o[1][1]), xytext=(o[0][0] + 0.02, o[0][1]),
                    arrowprops=dict(arrowstyle="->", color=MUTED, lw=0.8))
        for name in ("IQ3_XS", "Q3_K_S", "Q3_K_M"):
            x, y, _ = d[name]
            ax.text(x + 0.05, y, name, ha="left", va="center", color=INK, fontsize=7)
        ax.set_title(title, fontsize=8, color=INK)
        ax.set_xlim(11.9, 14.1)
        ax.set_xlabel("total GB (base + adapter)")
    axes[0].set_ylabel("KL divergence vs BF16 (nats/token)")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, frameon=False, loc="lower center", ncol=3, fontsize=7)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save(fig, "fig-ladder-27b")

def fig_gsq():
    # experiments/32-t117-gsq-head-to-head/results-40ch.txt and results-fineweb-40ch.txt
    data = {"IQ2 class (8.4 GB base)": {"wikitext-2": (0.2028, 0.1313, 0.1191),
                                        "FineWeb-Edu": (0.1524, 0.1452, 0.1349)},
            "IQ3 class (11.8–12.0 GB base)": {"wikitext-2": (0.05251, 0.0373, 0.0353),   # 0.052512: label rounds to 0.053
                                              "FineWeb-Edu": (0.0402, 0.0369, 0.0347)}}
    names = ["GSQ-RCO (trained)", "Unsloth dynamic (bare)", "bare + one-shot correction (+0.9 GB)"]
    cols = [C1, C2, C3]
    fig, axes = plt.subplots(1, 2, figsize=(6.6, 2.9))
    for ax, (title, d) in zip(axes, data.items()):
        for gi, (corpus, vals) in enumerate(d.items()):
            for si, v in enumerate(vals):
                x = gi + (si - 1) * 0.26
                ax.bar(x, v, width=0.24, color=cols[si], edgecolor="#fcfcfb", linewidth=1.5,
                       label=names[si] if gi == 0 else None)
                ax.text(x, v + max(vals) * 0.015, f"{v:.3f}", ha="center", va="bottom", fontsize=7, color=INK)
        ax.set_xticks([0, 1]); ax.set_xticklabels(list(d.keys()))
        ax.set_title(title, fontsize=8.5, color=INK, loc="left")
        ax.grid(axis="x", visible=False); ax.set_ylim(0, max(max(v) for v in d.values()) * 1.15)
    axes[0].set_ylabel("KL divergence vs BF16, 512-token context")
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, frameon=False, loc="lower center", ncol=3, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save(fig, "fig-gsq-kld", axes[0], "KL divergence vs BF16,\n512-token context", rect=(0, 0.08, 1, 1))

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True); fig_ladder(); fig_gsq(); print("figures written")
