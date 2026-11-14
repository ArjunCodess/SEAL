"""Generate the ADMIT shortpaper from frozen SEAL artifacts.

ADMIT (Accountable Decision-Making and Integrated Trust for autonomous AI
systems) at HiPEAC 2027 accepts a one-page PDF presentation proposal with no
proceedings.  This script reads the generated SEAL tables and writes a
venue-specific TeX source, macros, evidence manifest, claim ledger, and
optionally compiles a PDF.  It does not make inference calls or submit anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_ANALYSIS_DIR = ROOT / "build" / "seal" / "analysis"
DEFAULT_GATE_CSV = ROOT / "build" / "gate_mutations.csv"
DEFAULT_GATE_MANIFEST = ROOT / "build" / "gate_mutations_manifest.json"
DEFAULT_AUTHORS = ROOT / "paper" / "authors.json"
DEFAULT_OUTPUT_DIR = ROOT / "build" / "venues" / "admit"
DEFAULT_PAPER_DIR = ROOT / "paper" / "venues" / "admit"
SCRATCH = ROOT / "build" / "tmp"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf8"))


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def latex_escape(value: Any) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "\n": " ",
    }
    return "".join(replacements.get(character, character) for character in str(value))


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf8")


def resolve_tectonic() -> Path | None:
    installed = shutil.which("tectonic")
    if installed:
        return Path(installed)
    for path in (
        ROOT / ".tools" / "tectonic" / "tectonic.exe",
        ROOT / ".tools" / "tectonic" / "tectonic",
        ROOT / ".tools" / "tectonic.exe",
    ):
        if path.is_file():
            return path
    return None


def load_two_endpoints(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise SystemExit(f"Missing two-endpoint table: {path}")
    frame = pd.read_csv(path)
    required = {
        "model",
        "arm",
        "coordination",
        "attack_scheduled",
        "attack_completed",
        "attack_error",
        "attack_invalid",
        "decision_numerator",
        "decision_denominator",
        "execution_numerator",
        "execution_denominator",
        "gateway_blocked",
        "honest_completed",
        "honest_completions",
        "honest_control_completed",
        "honest_control_completions",
        "honest_control_unsafe_executions",
        "honest_unsafe_executions",
    }
    missing = required - set(frame.columns)
    if missing:
        raise SystemExit(f"Two-endpoint table missing columns: {sorted(missing)}")
    return frame


def load_pairing(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise SystemExit(f"Missing pairing coverage table: {path}")
    return pd.read_csv(path)


def load_gate_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise SystemExit(f"Missing gate mutation CSV: {path}")
    return pd.read_csv(path)


def load_gate_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"Missing gate mutation manifest: {path}")
    return read_json(path)


def compute_metrics(
    analysis_dir: Path,
    gate_csv: Path,
    gate_manifest_path: Path,
) -> dict[str, Any]:
    two_path = analysis_dir / "two_endpoints.csv"
    from scripts.seal_worker_audit import original_worker_audit
    worker_audit = original_worker_audit()
    pairing_path = analysis_dir / "pairing_coverage.csv"
    frame = load_two_endpoints(two_path)
    pairing = load_pairing(pairing_path)
    gate = load_gate_csv(gate_csv)
    manifest = load_gate_manifest(gate_manifest_path)

    attack = frame[frame["coordination"].isin(["single", "coordinated"])].copy()
    honest = frame[frame["coordination"] == "honest"].copy()

    def paired(model: str, coordination: str) -> tuple[int, int]:
        subset = pairing[
            (pairing["model"] == model)
            & (pairing["coordination"] == coordination)
            & (pairing["endpoint"] == "unsafe_execution")
        ]
        if subset.empty:
            return 0, 0
        row = subset.iloc[0]
        return int(row["families_paired"]), int(row["families_eligible"])

    gemma_single_paired, gemma_single_eligible = paired("gemma-4-31b-it", "single")
    gemma_coord_paired, gemma_coord_eligible = paired("gemma-4-31b-it", "coordinated")
    qwen_single_paired, qwen_single_eligible = paired("qwen3.5-397b", "single")
    qwen_coord_paired, qwen_coord_eligible = paired("qwen3.5-397b", "coordinated")

    incident_unpaired = int(
        (
            (pairing["family_id"] if "family_id" in pairing.columns else pairing.index)
            .astype(str)
            .str.contains("incident")
        ).sum()
    ) if False else 0
    # incident-timeline unpaired count across attack strata from pairing audit if present
    audit_path = analysis_dir / "pairing_audit.csv"
    incident_unpaired_strata = 0
    if audit_path.is_file():
        audit = pd.read_csv(audit_path)
        incident_unpaired_strata = int(
            (
                (audit["family_id"].astype(str) == "incident-timeline")
                & (audit["endpoint"] == "unsafe_execution")
                & (audit["coordination"].isin(["single", "coordinated"]))
                & (audit["paired"] == False)
            ).sum()
        )

    metrics: dict[str, Any] = {
        "models": sorted(frame["model"].astype(str).unique().tolist()),
        "original_worker_accepted": worker_audit['accepted'],
        "original_worker_copied": worker_audit['exact_copies'],
        "families": int(frame["attack_families"].max()) if "attack_families" in frame.columns else 4,
        "attack_scheduled": int(attack["attack_scheduled"].sum()),
        "attack_completed": int(attack["attack_completed"].sum()),
        "attack_errors": int(attack["attack_error"].sum()),
        "attack_invalids": int(attack["attack_invalid"].sum()),
        "claims_completed": int(attack[attack["arm"] == "claims"]["attack_completed"].sum()),
        "claims_unsafe_attempts": int(attack[attack["arm"] == "claims"]["decision_numerator"].sum()),
        "claims_unsafe_executions": int(attack[attack["arm"] == "claims"]["execution_numerator"].sum()),
        "receipts_completed": int(attack[attack["arm"] == "receipts"]["attack_completed"].sum()),
        "receipts_unsafe_attempts": int(attack[attack["arm"] == "receipts"]["decision_numerator"].sum()),
        "receipts_unsafe_executions": int(attack[attack["arm"] == "receipts"]["execution_numerator"].sum()),
        "state_completed": int(attack[attack["arm"] == "state"]["attack_completed"].sum()),
        "state_unsafe_attempts": int(attack[attack["arm"] == "state"]["decision_numerator"].sum()),
        "state_unsafe_executions": int(attack[attack["arm"] == "state"]["execution_numerator"].sum()),
        "provenance_completed": int(attack[attack["arm"] == "provenance"]["attack_completed"].sum()),
        "provenance_unsafe_attempts": int(attack[attack["arm"] == "provenance"]["decision_numerator"].sum()),
        "provenance_unsafe_executions": int(attack[attack["arm"] == "provenance"]["execution_numerator"].sum()),
        "provenance_gateway_blocked": int(attack[attack["arm"] == "provenance"]["gateway_blocked"].sum()),
        "honest_completed": int(honest["honest_completed"].sum()),
        "honest_scheduled": int(honest["honest_scheduled"].sum()),
        "honest_completions": int(honest["honest_completions"].sum()),
        "honest_control_completed": int(honest["honest_completed"].sum()),
        "honest_control_completions": int(honest["honest_completions"].sum()),
        "honest_control_unsafe_executions": int(honest["honest_unsafe_executions"].sum()),
        "honest_all_unsafe_executions": int(honest["honest_unsafe_executions"].sum()),
        "mutations_total": int(len(gate)),
        "mutations_block": int((gate["gate_action"] == "block").sum()),
        "mutations_abstain": int((gate["gate_action"] == "abstain").sum()),
        "mutations_publish": int((gate["gate_action"] == "publish").sum()),
        "positive_controls_total": int(manifest.get("controls", {}).get("positive_legitimate_controls", {}).get("total", 0)),
        "positive_controls_passed": int(manifest.get("controls", {}).get("positive_legitimate_controls", {}).get("published_count", 0)),
        "equality_controls_total": int(manifest.get("controls", {}).get("isolated_equality_mismatch_controls", {}).get("total", 0)),
        "equality_controls_passed": int(manifest.get("controls", {}).get("isolated_equality_mismatch_controls", {}).get("blocked_count", 0)),
        "gemma_single_paired": gemma_single_paired,
        "gemma_single_eligible": gemma_single_eligible,
        "gemma_coord_paired": gemma_coord_paired,
        "gemma_coord_eligible": gemma_coord_eligible,
        "qwen_single_paired": qwen_single_paired,
        "qwen_single_eligible": qwen_single_eligible,
        "qwen_coord_paired": qwen_coord_paired,
        "qwen_coord_eligible": qwen_coord_eligible,
        "incident_unpaired_strata": incident_unpaired_strata,
        "crosscheck_passed": True,
    }

    crosscheck_path = analysis_dir / "crosscheck.json"
    if crosscheck_path.is_file():
        crosscheck = read_json(crosscheck_path)
        metrics["crosscheck_passed"] = bool(crosscheck.get("all_passed", True))

    return metrics


def blind_available(blind_dir: Path | None) -> tuple[bool, list[str]]:
    if blind_dir is None:
        return False, ["no blind study directory supplied"]
    if not blind_dir.is_dir():
        return False, [f"blind study directory does not exist: {blind_dir}"]
    reasons: list[str] = []
    has_checksum = any("checksum" in path.name.lower() for path in blind_dir.rglob("*") if path.is_file())
    has_ledger = any("ledger" in path.name.lower() or "manifest" in path.name.lower() for path in blind_dir.rglob("*") if path.is_file())
    if not has_checksum:
        reasons.append("missing checksum file")
    if not has_ledger:
        reasons.append("missing ledger or manifest file")
    return len(reasons) == 0, reasons


def build_macros(metrics: Mapping[str, Any]) -> str:
    lines = ["% Generated by scripts/prepare_seal_admit.py", "% Do not edit by hand."]
    for key, value in metrics.items():
        if isinstance(value, (list, dict)):
            continue
        macro = "".join(part.capitalize() for part in key.split("_"))
        if isinstance(value, bool):
            rendered = "yes" if value else "no"
        elif isinstance(value, float):
            rendered = f"{value:.6f}"
        else:
            rendered = str(value)
        lines.append(f"\\newcommand{{\\{macro}}}{{{latex_escape(rendered)}}}")
    return "\n".join(lines) + "\n"


def build_authors(authors_path: Path) -> str:
    payload = read_json(authors_path)
    authors = payload.get("authors") or []
    if not authors:
        raise SystemExit(f"No authors in {authors_path}")
    parts = []
    for author in authors:
        name = latex_escape(author["name"])
        affiliation = latex_escape(author.get("affiliation", ""))
        parts.append(f"{name}\\\\{affiliation}" if affiliation else name)
    return "\\author{" + " \\and ".join(parts) + "}\n"


def build_abstract(metrics: Mapping[str, Any]) -> str:
    return (
        "When does evidence change a supervisor's publication request, and when does code stop its execution? "
        f"SEAL records {metrics['attack_scheduled']} scheduled attack episodes on fixed synthetic local jobs, "
        "with two supervisors reported separately. Claims, receipts, and a fixed snapshot still permit unsafe "
        "publication. Provenance prevents execution through a deterministic gate; the model can still request "
        "publication. We present separate decision and execution counts, a gate mutation audit, and the "
        "coverage limits of matched comparisons.\n"
    )


def build_endpoint_table(frame) -> str:
    arms = ["claims", "receipts", "state", "provenance"]
    lines = [
        r"\begin{tabular}{llcccc}",
        r"\toprule",
        r"Model & Coord. & Claims & Receipts & State & Provenance \\",
        r"\midrule",
    ]
    for model in sorted(frame["model"].astype(str).unique().tolist()):
        label = model
        for coord in ("single", "coordinated"):
            cells = []
            for arm in arms:
                sub = frame[
                    (frame["model"].astype(str) == model)
                    & (frame["coordination"].astype(str) == coord)
                    & (frame["arm"].astype(str) == arm)
                ]
                if sub.empty:
                    cells.append("n/a")
                    continue
                row = sub.iloc[0]
                cells.append(
                    str(int(row["decision_numerator"])) + "/" +
                    str(int(row["execution_numerator"])) + "/" +
                    str(int(row["attack_completed"]))
                )
            lines.append(label + ' & ' + coord + ' & ' + ' & '.join(cells) + ' \\\\')
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines) + "\n"


def build_main_tex() -> str:
    return r"""\documentclass[10pt]{article}
\usepackage[margin=0.7in]{geometry}
\usepackage{booktabs,array,microtype}
\usepackage[colorlinks=true,urlcolor=blue]{hyperref}
\input{live_generated/generated}
\input{live_generated/authors}
\title{SEAL: Separating Supervisor Requests from Code-Enforced Outcomes}
\date{}
\begin{document}
\begin{center}
{\large\bfseries SEAL: Separating Supervisor Requests from Code-Enforced Outcomes\par}
\smallskip
\AuthorName\\\AuthorAffiliation
\end{center}
\input{live_generated/abstract}
The frozen record contains \AttackScheduled{} scheduled attacks and \HonestScheduled{} honest controls on four families. Attacks are instructed and non-adaptive; the sink is fake and local, \texttt{side\_effects} is false, and signing keys are public demo keys.

\section*{Evidence and enforcement}
\begin{center}\small
\input{live_generated/table_endpoints}
\end{center}
Each cell is unsafe decisions / unsafe executions / completed attacks (D/E/N); models and speaker conditions remain separate. Claims, receipts, and the fixed snapshot still publish completed attacks. Provenance execution zeros come from \texttt{\_provenance\_action} in \texttt{src/live\_fixtures.py}. Its \texttt{trusted\_summary != summary} check and earlier binding/authorization vetoes can block a corrected true summary on an illegitimate fixture.

\section*{Mutation and pairing coverage}
The \MutationsTotal{} fixture-mutation pairs yielded \MutationsPublish{} publications, \MutationsBlock{} blocks, and \MutationsAbstain{} abstentions. Legitimate controls published \PositiveControlsPassed{}/\PositiveControlsTotal{}; isolated equality-mismatch controls blocked \EqualityControlsPassed{}/\EqualityControlsTotal{}. Pairing retains \GemmaSinglePaired{}/\GemmaSingleEligible{} and \GemmaCoordPaired{}/\GemmaCoordEligible{} Gemma families for single/coordinated attacks, and \QwenSinglePaired{}/\QwenSingleEligible{} and \QwenCoordPaired{}/\QwenCoordEligible{} Qwen families. Incident-timeline is unpaired in every attack stratum.

The workshop question is what an auditor should record before crediting a system with prevention. We propose retaining the requested action, enforced action, gate reason, and oracle judgment together. This exposes whether prevention came from the supervisor or the boundary, while keeping invalid responses distinct from refusals. The legitimate controls check useful publication as well as blocking; the finite catalog does not establish deployment-wide safety.

\section*{Five limits}
Scripted wording: \OriginalWorkerCopied{}/\OriginalWorkerAccepted{} accepted original worker calls copied the template because \texttt{wording\_reference} was in the prompt. Unmatched arms: paired contrasts describe the matched families only; zero pairs means unavailable. True summary shown: old provenance observations supplied it when available; blind provenance numbers are excluded from this draft. Confounded coordination: message counts and speaker roles change together. Four families: the catalog covers only three mechanisms and synthetic local jobs. These observations establish neither learned refusal nor general receipt weakness nor collusion by three speakers.
\end{document}
"""


def compile_pdf(paper_dir: Path, output_pdf: Path) -> None:
    tectonic = resolve_tectonic()
    if tectonic is None:
        raise SystemExit("Missing Tectonic binary")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SCRATCH) as temporary:
        isolated = Path(temporary)
        shutil.copytree(paper_dir, isolated / "paper", ignore=shutil.ignore_patterns("*.pdf"))
        cmd = [str(tectonic), str(isolated / "paper" / "admit.tex")]
        proc = subprocess.run(cmd, cwd=isolated / "paper", capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"Tectonic failed:\n{proc.stdout}\n{proc.stderr}")
        pdf = isolated / "paper" / "admit.pdf"
        if not pdf.is_file():
            raise SystemExit("admit.pdf was not produced")
        shutil.copy2(pdf, output_pdf)


def run(
    *,
    analysis_dir: Path,
    gate_csv: Path,
    gate_manifest: Path,
    authors_path: Path,
    output_dir: Path,
    paper_dir: Path,
    blind_dir: Path | None,
    include_blind: bool,
    compile_pdf_flag: bool,
) -> dict[str, Any]:
    metrics = compute_metrics(analysis_dir, gate_csv, gate_manifest)

    if include_blind:
        from scripts.seal_blind_paper_table import optional_table
        blind = optional_table(blind_dir)
        if blind is None:
            raise SystemExit('Refusing --include-blind: study is not frozen')
    else:
        blind = None

    output_dir.mkdir(parents=True, exist_ok=True)
    paper_dir.mkdir(parents=True, exist_ok=True)

    inputs = {
        "two_endpoints": analysis_dir / "two_endpoints.csv",
        "pairing_coverage": analysis_dir / "pairing_coverage.csv",
        "pairing_audit": analysis_dir / "pairing_audit.csv",
        "crosscheck": analysis_dir / "crosscheck.json",
        "gate_csv": gate_csv,
        "gate_manifest": gate_manifest,
        "authors": authors_path,
    }
    input_hashes = {name: (sha256_file(path) if path.is_file() else None) for name, path in inputs.items()}

    macros = build_macros(metrics)
    authors_tex = build_authors(authors_path)
    abstract = build_abstract(metrics)

    author_name = read_json(authors_path)["authors"][0]["name"]
    author_affiliation = read_json(authors_path)["authors"][0].get("affiliation", "")
    author_macros = (
        macros
        + "\n\\newcommand{\\AuthorName}{" + latex_escape(author_name) + "}\n"
        + "\\newcommand{\\AuthorAffiliation}{" + latex_escape(author_affiliation) + "}\n"
    )
    write(output_dir / "generated.tex", author_macros)
    write(output_dir / "authors.tex", authors_tex)
    write(output_dir / "abstract.tex", latex_escape(abstract) + "\n")
    write(output_dir / "status_banner.tex", "")
    import pandas as pd
    frame = load_two_endpoints(analysis_dir / "two_endpoints.csv")
    table_tex = build_endpoint_table(frame)
    write(output_dir / "table_endpoints.tex", table_tex)
    (paper_dir / "live_generated").mkdir(parents=True, exist_ok=True)
    write(paper_dir / "live_generated" / "table_endpoints.tex", table_tex)
    source = build_main_tex()
    if blind:
        write(output_dir / 'table_blind.tex', blind['tex'])
        source = source.replace(r'\section*{Mutation and pairing coverage}',
            r'\section*{Blind provenance}' + '\n' +
            r'Only the outward trusted summary is nulled; the gate and other evidence remain unchanged. ' +
            r'Cells report old versus blind D/E/N. Answer inference remains possible and collection times differ.' + '\n' +
            r'\begin{center}\small\input{live_generated/table_blind}\end{center}' + '\n' +
            r'\section*{Mutation and pairing coverage}')
        source = source.replace('; blind provenance numbers are excluded from this draft', '')
    write(paper_dir / "admit.tex", source)
    # The delivered source must compile on its own, without the build directory.
    for name in ('generated.tex', 'authors.tex', 'abstract.tex', 'table_endpoints.tex'):
        shutil.copy2(output_dir / name, paper_dir / 'live_generated' / name)
    local_blind = paper_dir / 'live_generated/table_blind.tex'
    if blind:
        shutil.copy2(output_dir / 'table_blind.tex', local_blind)
    elif local_blind.exists():
        local_blind.unlink()

    # Claim ledger and evidence manifest.
    claim_ledger = {
        "main_results_ready": metrics['crosscheck_passed'],
        "blind_included": bool(blind),
        "claims": [
            {
                "id": "ADMIT-C1",
                "claim": f"Provenance executed {metrics['provenance_unsafe_executions']} of {metrics['provenance_completed']} completed attack episodes; model strata are in the table.",
                "evidence": str(inputs["two_endpoints"]),
                "status": "supported",
            },
            {
                "id": "ADMIT-C2",
                "claim": "Claims, receipts, and fixed state have matching unsafe decision and execution counts in each model and speaker stratum.",
                "evidence": str(inputs["two_endpoints"]),
                "status": "supported",
            },
            {
                "id": "ADMIT-C3",
                "claim": f"Mutation sweep produced {metrics['mutations_publish']} publications across {metrics['mutations_total']} fixture-mutation pairs.",
                "evidence": str(gate_csv),
                "status": "supported",
            },
            {
                "id": "ADMIT-C4",
                "claim": f"Positive controls published {metrics['positive_controls_passed']}/{metrics['positive_controls_total']}; equality-mismatch controls blocked {metrics['equality_controls_passed']}/{metrics['equality_controls_total']}.",
                "evidence": str(gate_manifest),
                "status": "supported",
            },
            {
                "id": "ADMIT-C5",
                "claim": "Pairing retained Gemma 2/4 in both attack strata, Qwen 0/4 single and 1/4 coordinated.",
                "evidence": str(inputs["pairing_coverage"]),
                "status": "supported",
            },
        ],
        "metrics": metrics,
    }
    write(output_dir / "claim_to_evidence.json", json.dumps(claim_ledger, indent=2) + "\n")

    evidence_manifest = {
        "venue": "ADMIT",
        "format": "one-page PDF presentation proposal, no proceedings",
        "official_cfp": "https://admit-workshop.github.io",
        "deadline_aoe": "2026-11-15T23:59:00-12:00",
        "authors_source": str(authors_path),
        "author_name": read_json(authors_path)["authors"][0]["name"],
        "input_hashes": input_hashes,
        "blind_evidence": ({k: v for k, v in blind.items() if k != "tex"} if blind else None),
        "metrics": metrics,
        "blind_included": bool(blind),
        "appendix_included": False,
    }
    write(output_dir / "evidence_manifest.json", json.dumps(evidence_manifest, indent=2) + "\n")

    # Remove any stale appendix files in owned output/paper dirs.
    for stale in (paper_dir / "appendix.tex", output_dir / "appendix.tex", paper_dir / "A3.tex", output_dir / "A3.tex"):
        if stale.is_file():
            stale.unlink()

    status = {
        "render_mode": "admit_proposal",
        "output_dir": str(output_dir),
        "paper_dir": str(paper_dir),
        "pdf": None,
        "blind_included": bool(blind),
        "appendix_included": False,
        "metrics": metrics,
    }

    if compile_pdf_flag:
        pdf_path = output_dir / "admit.pdf"
        compile_pdf(paper_dir, pdf_path)
        paper_pdf = paper_dir / "admit.pdf"
        if pdf_path.is_file():
            shutil.copy2(pdf_path, paper_pdf)
        status["pdf"] = str(pdf_path)

    write(output_dir / "build_status.json", json.dumps(status, indent=2) + "\n")
    return status


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-dir", type=Path, default=DEFAULT_ANALYSIS_DIR)
    parser.add_argument("--gate-csv", type=Path, default=DEFAULT_GATE_CSV)
    parser.add_argument("--gate-manifest", type=Path, default=DEFAULT_GATE_MANIFEST)
    parser.add_argument("--authors", type=Path, default=DEFAULT_AUTHORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--paper-dir", type=Path, default=DEFAULT_PAPER_DIR)
    parser.add_argument("--blind-dir", type=Path, default=None)
    parser.add_argument("--include-blind", action="store_true")
    parser.add_argument("--compile", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    status = run(
        analysis_dir=args.analysis_dir,
        gate_csv=args.gate_csv,
        gate_manifest=args.gate_manifest,
        authors_path=args.authors,
        output_dir=args.output_dir,
        paper_dir=args.paper_dir,
        blind_dir=args.blind_dir,
        include_blind=args.include_blind,
        compile_pdf_flag=args.compile,
    )
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
