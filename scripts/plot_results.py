"""Draw a static shareable figure from the recorded per-instance results."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads((args.results / "results/summary.json").read_text())
    methods = ["independent", "static", "interaction"]
    colors = ["#989fa8", "#505b68", "#24719e"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), layout="constrained")
    fig.suptitle("InfiniDojo v0: procedural pickup scene generation", fontsize=14)
    for ax, metric, title in zip(
        axes,
        ["native_success", "usable"],
        ["Native reference lift success", "Conservative usable-scene yield"],
    ):
        for i, method in enumerate(methods):
            data = summary["by_method"][method]["by_clutter_count"]
            count = np.array([data[str(k)][metric] for k in [0, 6, 12]])
            ax.bar(np.arange(3) + (i - 1) * 0.25, count / 6 * 100, width=0.23, label=method, color=colors[i])
        ax.set(
            title=title,
            ylabel="Successful requested scenes (%)",
            xlabel="Requested clutter objects (count)",
            xticks=np.arange(3),
            xticklabels=["0", "6", "12"],
            ylim=(0, 110),
        )
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=3)
    fig.text(
        0.01,
        -0.06,
        "Source: frozen native RoboDojo runs, 2026-10-12. Six paired seeds per condition. "
        "Usable requires task success, initialization fidelity and sampled collision-box audits.",
        fontsize=8,
    )
    fig.savefig(args.results / "comparison.png", dpi=180, bbox_inches="tight")
    fig.savefig(args.results / "comparison.pdf", bbox_inches="tight")


if __name__ == "__main__":
    main()
