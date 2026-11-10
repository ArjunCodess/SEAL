"""Render the retained local-tokenizer measurements as a standalone figure."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    measurement = json.loads(args.measurement.read_text(encoding="utf-8"))
    summary = measurement["summary"]
    rows = sorted((r for r in measurement["group_units"] if r["input_tokens"] is not None), key=lambda r: r["input_tokens"])
    limit = summary["context_constraint"]["max_input_tokens"]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    fig.patch.set_facecolor("white")
    for rank, row in enumerate(rows, 1):
        tokens = row["input_tokens"]
        ax.vlines(rank, 0, tokens, color="#dbe3e9", linewidth=1.5)
        ax.scatter(rank, tokens, s=30, color="#a3452c" if tokens > limit else "#28536f", zorder=3)
    ax.axhline(limit, color="#a3452c", linestyle="--", linewidth=1.2)
    ax.text(0.02, limit + 3500, f"Configured input allowance: {limit:,}", color="#843b29", transform=ax.get_yaxis_transform(), fontsize=10)
    ax.set(title="Full-record context use in 30 software-agent workflows", xlabel="Workflow rank by prompt length", ylabel="Full audit prompt tokens")
    ax.set_xlim(0, len(rows) + 1)
    ax.set_ylim(0, max(limit, max(r["input_tokens"] for r in rows)) * 1.12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:.0f}k"))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.15)
    fig.text(0.12, 0.02, "Pinned Qwen 3.5 tokenizer; one archive/model stratum; local counts are not provider usage.", fontsize=9, color="#535b60")
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220, facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
