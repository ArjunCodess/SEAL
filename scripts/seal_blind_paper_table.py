"""Verified optional blind-arm table shared by the two short submissions."""
import csv
from pathlib import Path

from src import seal_collection as sc


def optional_table(study=None):
    directory = Path(study) if study else sc.DEFAULT_STUDY_DIR
    if not (directory / "freeze_manifest.json").is_file():
        return None
    manifest = sc.read_json(directory / "freeze_manifest.json")
    if manifest["scheduled_episode_count"] != 240 or manifest["pending"]:
        raise ValueError("Blind study is not a complete terminal grid")
    sc.verify_study_checksums(directory)
    target = sc.ROOT / "build/seal/analysis/provenance_comparison.csv"
    sc.build_comparison(study_dir=directory, output_path=target)
    with target.open(encoding="utf8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    lines = [r"\begin{tabular}{@{}llcc@{}}", r"\toprule",
             r"Model & Coord. & Old D/E/N & Blind D/E/N \\\midrule"]
    for row in rows:
        if row["condition"] == "honest":
            continue
        counts = ["/".join(row[f"{prefix}_{field}"] for field in
                           ("decision_unsafe_attempt_count", "execution_unsafe_count", "completed"))
                  for prefix in ("old", "blind")]
        lines.append(" & ".join([row["model"], row["condition"], *counts]) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return {"tex": "\n".join(lines) + "\n", "study_checksums_sha256": sc.sha256_file(directory / "checksums.json"),
            "comparison_sha256": sc.sha256_file(target), "comparison": str(target)}
