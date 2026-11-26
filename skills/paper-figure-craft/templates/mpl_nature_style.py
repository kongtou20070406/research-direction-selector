"""Nature / ICLR publication style Matplotlib presets (v5.1: crisp fine strokes, pure black ink)."""

from __future__ import annotations

import matplotlib.pyplot as plt


PALETTE = {
    "ours_teal": "#007257",        # Ours (T_tr <= 64) / RecWM
    "ours_teal_light": "#D9EFE8",
    "ours_blue": "#005AB5",        # Ours (T_tr <= 12)
    "cwm_coral": "#D55E00",        # Additive CWM RT / vermilion shock alert
    "cwm_coral_light": "#FCE8DC",
    "nonrec_slate": "#666666",     # Non-recurrent baselines (uniform neutral gray)
    "muted_gray": "#999999",
    "ink": "#000000",              # Pure black text, spines, ticks
    "oak_light": "#EFE6D5",
    "oak_edge": "#8C6D46",
}


def apply_nature_rcparams(base_fontsize: float = 7.6) -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
        "mathtext.fontset": "stix",
        "font.size": base_fontsize,
        "text.color": "#000000",
        "axes.titlesize": base_fontsize + 0.6,
        "axes.labelsize": base_fontsize,
        "axes.labelcolor": "#000000",
        "xtick.labelsize": base_fontsize - 0.6,
        "ytick.labelsize": base_fontsize - 0.6,
        "legend.fontsize": base_fontsize - 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "#000000",
        "axes.linewidth": 0.50,
        "xtick.color": "#000000",
        "ytick.color": "#000000",
        "xtick.major.width": 0.50,
        "ytick.major.width": 0.50,
        "xtick.major.size": 2.4,
        "ytick.major.size": 2.4,
        "axes.grid": False,
        "lines.linewidth": 0.95,
        "lines.markersize": 2.6,
        "svg.fonttype": "path",
        "figure.facecolor": "#FFFFFF",
        "axes.facecolor": "#FFFFFF",
    })


def plot_ci_ribbon(ax, x, mean, ci_lo, ci_hi, color: str, label: str, marker: str = "o", ls: str = "-", lw: float = 0.95):
    ax.fill_between(x, ci_lo, ci_hi, color=color, alpha=0.08, linewidth=0.25, edgecolor=color)
    line = ax.plot(x, mean, linestyle=ls, marker=marker, color=color, linewidth=lw,
                   markersize=2.6, markeredgewidth=0.0, label=label)
    return line[0]


def add_direct_label(ax, x: float, y: float, text: str, color: str, ha: str = "left", va: str = "center", fontsize: float = 7.0, bold: bool = False):
    ax.text(x, y, text, color=color, ha=ha, va=va, fontsize=fontsize,
            fontweight=("bold" if bold else "normal"))


def add_panel_letter(ax, letter: str, x: float = -0.14, y: float = 1.07, fontsize: float = 9.5) -> None:
    ax.text(x, y, letter, transform=ax.transAxes, fontsize=fontsize, fontweight="bold",
            color="#000000", va="top", ha="left")
