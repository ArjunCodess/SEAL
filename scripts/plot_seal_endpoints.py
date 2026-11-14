"""Publication figure of requests versus executions, with no model pooling."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def run():
    frame = pd.read_csv(ROOT / "build/seal/analysis/two_endpoints.csv")
    arms = ("claims", "receipts", "state", "provenance")
    models = sorted(frame.model.unique())
    fig, axes = plt.subplots(2, 2, figsize=(8, 5.4), sharex=True)
    for model_index, model in enumerate(models):
        for condition_index, condition in enumerate(("single", "coordinated")):
            axis = axes[model_index, condition_index]
            rows = frame[(frame.model == model) & (frame.coordination == condition)].set_index("arm")
            labels = []
            for y, arm in enumerate(arms):
                row = rows.loc[arm]
                decision = float(row.decision_rate)
                execution = float(row.execution_rate)
                axis.plot([execution, decision], [y, y], color="#9aa4ad", lw=2, zorder=1)
                axis.scatter([decision], [y + .045], color="#176a9e", marker="o", s=36,
                             label="Unsafe decision" if y == 0 else None, zorder=3)
                axis.scatter([execution], [y - .045], color="#d56a20", marker="s", s=27,
                             label="Unsafe execution" if y == 0 else None, zorder=3)
                labels.append(f"{arm} (n={int(row.attack_completed)})")
            axis.set_yticks(range(4), labels, fontsize=8)
            axis.set_ylim(3.5, -.5)
            axis.set_xlim(-.04, 1.04)
            axis.set_title(f"{model}\n{condition}", fontsize=9, loc="left")
            axis.grid(axis="x", alpha=.2)
            axis.spines[["top", "right", "left"]].set_visible(False)
            axis.tick_params(axis="y", length=0)
            axis.set_xticks([0, .25, .5, .75, 1], ["0", "25", "50", "75", "100"])
    for axis in axes[-1]:
        axis.set_xlabel("Completed-attack rate (%)", fontsize=9)
    fig.legend(*axes[0, 0].get_legend_handles_labels(), loc="lower center", ncol=2,
               frameon=False, bbox_to_anchor=(.5, -.005), fontsize=9)
    fig.tight_layout(rect=(0, .05, 1, 1))
    out = ROOT / "build/seal/figures"
    out.mkdir(parents=True, exist_ok=True)
    for extension in ("pdf", "png"):
        fig.savefig(out / f"two_endpoints.{extension}", dpi=220, bbox_inches="tight")
    plt.close(fig)
    return out / "two_endpoints.pdf"


if __name__ == "__main__":
    print(run())
