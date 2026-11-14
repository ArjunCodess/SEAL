"""Prepare venue-specific submission artifacts from frozen SEAL offline results.

The generator is intentionally venue-aware so later venues can reuse it, but
only the AGENTVERIFY content is implemented today. No inference, network calls,
or credential use occurs here; all numbers are derived from the frozen study
inputs and generated SEAL tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "study"
DEFAULT_BUILD = ROOT / "build"
DEFAULT_PAPER = ROOT / "paper" / "venues"


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_frozen_inputs() -> dict[str, Any]:
    """Verify the frozen study inputs and pinned source contract."""
    sys.path.insert(0, str(ROOT))
    from scripts.reproduce import verify_inputs, verify_recovery_inputs

    result = verify_inputs(source_root=ROOT, legacy_only=False)
    result.update(verify_recovery_inputs())
    return result


def ensure_derived_artifacts(build_root: Path = DEFAULT_BUILD) -> None:
    """Ensure the frozen SEAL derived artifacts exist, rebuilding offline if needed."""
    needed = [
        build_root / "episodes.jsonl",
        build_root / "seal" / "analysis" / "two_endpoints.csv",
        build_root / "seal" / "analysis" / "pairing_coverage.csv",
        build_root / "seal" / "analysis" / "crosscheck.json",
        build_root / "gate_mutations.csv",
        build_root / "gate_mutations_manifest.json",
    ]
    if all(path.is_file() for path in needed):
        return
    sys.path.insert(0, str(ROOT))
    from scripts.reproduce import reproduce

    reproduce(build_root, analysis_only=True)


def load_derived(build_root: Path = DEFAULT_BUILD) -> dict[str, Any]:
    """Load and validate the frozen SEAL derived artifacts."""
    ensure_derived_artifacts(build_root)
    for mod_name in list(sys.modules):
        if mod_name == "src" or mod_name.startswith("src."):
            del sys.modules[mod_name]
    sys.path.insert(0, str(ROOT))
    import pandas as pd

    from scripts.reproduce import DATA
    from src.live_analysis import load_jsonl
    from src.seal_analysis import two_endpoint_crosscheck

    episodes_path = build_root / "episodes.jsonl"
    records, blank_lines = load_jsonl(episodes_path)
    status_counts: dict[str, int] = {}
    for record in records:
        status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1

    if len(records) != 960:
        raise ValueError(f"Expected 960 episodes, found {len(records)}")
    if status_counts != {"completed": 909, "invalid": 29, "error": 22}:
        raise ValueError(f"Unexpected status counts: {status_counts}")

    two_endpoints = pd.read_csv(build_root / "seal" / "analysis" / "two_endpoints.csv")
    pairing = pd.read_csv(build_root / "seal" / "analysis" / "pairing_coverage.csv")
    crosscheck = json.loads((build_root / "seal" / "analysis" / "crosscheck.json").read_text(encoding="utf8"))
    gate_manifest = json.loads((build_root / "gate_mutations_manifest.json").read_text(encoding="utf8"))
    gate_csv = pd.read_csv(build_root / "gate_mutations.csv")

    if not crosscheck.get("all_passed"):
        raise ValueError("SEAL two-endpoint crosscheck failed")
    if len(gate_csv) != 400:
        raise ValueError(f"Expected 400 gate mutations, found {len(gate_csv)}")
    actions = gate_manifest["observed_gate_actions"]
    if actions.get("block") != 300 or actions.get("abstain") != 100 or actions.get("publish", 0) != 0:
        raise ValueError(f"Unexpected gate mutation actions: {actions}")
    if gate_manifest.get("attack_unsafe_executions") != 0:
        raise ValueError("Gate mutation unsafe executions must be zero")
    if not gate_manifest["controls"]["positive_legitimate_controls"]["all_passed"]:
        raise ValueError("Positive gate controls failed")
    if not gate_manifest["controls"]["isolated_equality_mismatch_controls"]["all_passed"]:
        raise ValueError("Equality-mismatch gate controls failed")

    # Pairing invariants from the frozen study.
    lookup = {(r["model"], r["coordination"], r["endpoint"]): r for _, r in pairing.iterrows()}
    expected_pairs = {
        ("gemma-4-31b-it", "single", "unsafe_execution"): 2,
        ("gemma-4-31b-it", "coordinated", "unsafe_execution"): 2,
        ("qwen3.5-397b", "single", "unsafe_execution"): 0,
        ("qwen3.5-397b", "coordinated", "unsafe_execution"): 1,
    }
    for key, expected in expected_pairs.items():
        if int(lookup[key]["families_paired"]) != expected:
            raise ValueError(f"Unexpected pairing count for {key}: {lookup[key]['families_paired']} != {expected}")
    incident = pairing[
        (pairing["family_id"] if "family_id" in pairing else False) == "incident-timeline"
    ] if False else None
    # The per-family audit is the source of the incident-timeline exclusion.
    audit_path = build_root / "seal" / "analysis" / "pairing_audit.csv"
    audit = pd.read_csv(audit_path)
    incident_rows = audit[
        (audit["family_id"] == "incident-timeline")
        & (audit["endpoint"] == "unsafe_execution")
        & (audit["coordination"].isin(["single", "coordinated"]))
    ]
    if len(incident_rows) != 4 or not (~incident_rows["paired"]).all():
        raise ValueError("incident-timeline must be unpaired in every attack stratum")

    # Recompute the crosscheck from the saved episodes as an independent guard.
    recomputed = two_endpoint_crosscheck(records)
    if not recomputed.get("all_passed"):
        raise ValueError("Independent two-endpoint crosscheck failed")

    return {
        "records": records,
        "blank_lines": blank_lines,
        "status_counts": status_counts,
        "two_endpoints": two_endpoints,
        "pairing": pairing,
        "pairing_audit": audit,
        "crosscheck": crosscheck,
        "gate_manifest": gate_manifest,
        "gate_csv": gate_csv,
        "build_root": build_root,
    }


def _int(value: Any) -> int:
    return int(value)


def compute_macros(derived: dict[str, Any]) -> dict[str, str]:
    from scripts.seal_worker_audit import original_worker_audit
    worker_audit = original_worker_audit()
    two = derived["two_endpoints"]
    gate_manifest = derived["gate_manifest"]
    gate_csv = derived["gate_csv"]
    pairing_audit = derived["pairing_audit"]
    incident_rows = pairing_audit[
        (pairing_audit["family_id"] == "incident-timeline")
        & (pairing_audit["endpoint"] == "unsafe_execution")
        & (pairing_audit["coordination"].isin(["single", "coordinated"]))
    ]

    attack_two = two[two["coordination"].isin(["single", "coordinated"])]
    provenance = attack_two[attack_two["arm"] == "provenance"]
    prov_decisions = int(provenance["decision_numerator"].sum())
    prov_executions = int(provenance["execution_numerator"].sum())

    true_rows = gate_csv[gate_csv["mutation"] == "true"]
    true_total = int(len(true_rows))
    true_blocked = int(((true_rows["gate_action"] == "block") | (true_rows["gate_action"] == "abstain")).sum())
    true_unsafe = int(true_rows["unsafe_execution"].sum())

    lookup = {
        (r["model"], r["coordination"], r["endpoint"]): r
        for _, r in derived["pairing"].iterrows()
    }
    gemma_single = int(lookup[("gemma-4-31b-it", "single", "unsafe_execution")]["families_paired"])
    gemma_coord = int(lookup[("gemma-4-31b-it", "coordinated", "unsafe_execution")]["families_paired"])
    qwen_single = int(lookup[("qwen3.5-397b", "single", "unsafe_execution")]["families_paired"])
    qwen_coord = int(lookup[("qwen3.5-397b", "coordinated", "unsafe_execution")]["families_paired"])

    reasons = gate_manifest["observed_gate_reasons"]
    actions = gate_manifest["observed_gate_actions"]
    macros = {
        "SealTotalEpisodes": str(len(derived["records"])),
        "SealCompleted": str(derived["status_counts"]["completed"]),
        "SealInvalid": str(derived["status_counts"]["invalid"]),
        "SealError": str(derived["status_counts"]["error"]),
        "ProvenanceDecisions": str(prov_decisions),
        "ProvenanceExecutions": str(prov_executions),
        "ProvenanceGap": str(prov_decisions - prov_executions),
        "MutationTotal": str(int(gate_manifest["total_mutations_evaluated"])),
        "MutationBlocked": str(int(actions.get("block", 0))),
        "MutationAbstained": str(int(actions.get("abstain", 0))),
        "MutationPublished": str(int(actions.get("publish", 0))),
        "MutationTrueRows": str(true_total),
        "MutationTrueBlocked": str(true_blocked),
        "MutationTrueUnsafe": str(true_unsafe),
        "PositiveControls": str(int(gate_manifest["controls"]["positive_legitimate_controls"]["total"])),
        "PositivePublished": str(int(gate_manifest["controls"]["positive_legitimate_controls"]["published_count"])),
        "MismatchControls": str(int(gate_manifest["controls"]["isolated_equality_mismatch_controls"]["total"])),
        "MismatchBlocked": str(int(gate_manifest["controls"]["isolated_equality_mismatch_controls"]["blocked_count"])),
        "PairGemmaSingle": str(gemma_single),
        "PairGemmaCoordinated": str(gemma_coord),
        "PairQwenSingle": str(qwen_single),
        "PairQwenCoordinated": str(qwen_coord),
        "WorkersCopied": str(worker_audit['exact_copies']),
        "WorkersTotal": str(worker_audit['accepted']),
        "SummaryUnbound": str(int(reasons.get("summary_unbound", 0))),
        "PublicationUnauthorized": str(int(reasons.get("publication_unauthorized", 0))),
        "ProvenanceIncomplete": str(int(reasons.get("provenance_incomplete", 0))),
        "IncidentUnpairedStrata": str(int(len(incident_rows))),
    }
    return macros


def _latex_escape(value: Any) -> str:
    text = str(value)
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
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def build_endpoint_table(two: Any) -> str:
    import pandas as pd

    rows = []
    pairs = two[two["coordination"].isin(["single", "coordinated"])][["model", "coordination"]].drop_duplicates()
    for _, pair in pairs.sort_values(["model", "coordination"]).iterrows():
        model = str(pair["model"])
        coord = "single" if pair["coordination"] == "single" else "coord."
        cells = []
        for arm in ("claims", "receipts", "state", "provenance"):
            sub = two[
                (two["model"] == pair["model"])
                & (two["coordination"] == pair["coordination"])
                & (two["arm"] == arm)
            ].iloc[0]
            cells.append(
                f"{int(sub['decision_numerator'])}/{int(sub['execution_numerator'])}/"
                f"{int(sub['decision_denominator'])}"
            )
        rows.append((model, coord, *cells))

    lines = [
        r"\begin{tabular}{@{}llcccc@{}}",
        r"\toprule",
        r"Model & Coord. & Claims & Receipts & State & Provenance \\",
        r"\midrule",
    ]
    for model, coord, *cells in rows:
        lines.append(f"{_latex_escape(model)} & {_latex_escape(coord)} & " + " & ".join(cells) + r" \\")
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines) + "\n"


def build_mutation_table(gate_manifest: dict[str, Any], gate_csv: Any) -> str:
    actions = gate_manifest["observed_gate_actions"]
    reasons = gate_manifest["observed_gate_reasons"]
    true_rows = gate_csv[gate_csv["mutation"] == "true"]
    lines = [
        r"\begin{tabular}{@{}lc@{}}",
        r"\toprule",
        r"Observed gate outcome or reason & Count \\",
        r"\midrule",
        f"Block & {int(actions.get('block', 0))} \\\\",
        f"Abstain & {int(actions.get('abstain', 0))} \\\\",
        f"Publish & {int(actions.get('publish', 0))} \\\\",
        r"\midrule",
        f"summary\\_unbound & {int(reasons.get('summary_unbound', 0))} \\\\",
        f"publication\\_unauthorized & {int(reasons.get('publication_unauthorized', 0))} \\\\",
        f"provenance\\_incomplete & {int(reasons.get('provenance_incomplete', 0))} \\\\",
        r"\midrule",
        f"true-summary mutations (blocked/abstained) & {int(((true_rows['gate_action'] == 'block') | (true_rows['gate_action'] == 'abstain')).sum())}/{int(len(true_rows))} \\\\",
        f"legitimate positive controls (published) & {int(gate_manifest['controls']['positive_legitimate_controls']['published_count'])}/{int(gate_manifest['controls']['positive_legitimate_controls']['total'])} \\\\",
        f"equality-mismatch controls (blocked) & {int(gate_manifest['controls']['isolated_equality_mismatch_controls']['blocked_count'])}/{int(gate_manifest['controls']['isolated_equality_mismatch_controls']['total'])} \\\\",
        r"\bottomrule",
        r"\end{tabular}",
    ]
    return "\n".join(lines) + "\n"


def build_pairing_table(pairing: Any) -> str:
    lookup_rows = pairing[pairing["endpoint"] == "unsafe_execution"]
    lines = [
        r"\begin{tabular}{@{}llccl@{}}",
        r"\toprule",
        r"Model & Coord. & Eligible & Paired & Unpaired reason \\",
        r"\midrule",
    ]
    for _, row in lookup_rows[lookup_rows["coordination"].isin(["single", "coordinated"])].sort_values(
        ["model", "coordination"]
    ).iterrows():
        model = str(row["model"])
        coord = "single" if row["coordination"] == "single" else "coord."
        lines.append(
            f"{_latex_escape(model)} & {_latex_escape(coord)} & "
            f"{int(row['families_eligible'])} & {int(row['families_paired'])} & "
            f"set mismatch ({int(row['families_unpaired'])}) \\\\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    return "\n".join(lines) + "\n"


def build_abstract(macros: dict[str, str]) -> str:
    total = macros["SealTotalEpisodes"]
    completed = macros["SealCompleted"]
    invalid = macros["SealInvalid"]
    errors = macros["SealError"]
    prov_dec = macros["ProvenanceDecisions"]
    prov_exec = macros["ProvenanceExecutions"]
    mutation_total = macros["MutationTotal"]
    mutation_blocked = macros["MutationBlocked"]
    mutation_abstained = macros["MutationAbstained"]
    mutation_published = macros["MutationPublished"]
    positive = macros["PositiveControls"]
    mismatch = macros["MismatchControls"]
    gemma_single = macros["PairGemmaSingle"]
    qwen_single = macros["PairQwenSingle"]
    qwen_coord = macros["PairQwenCoordinated"]
    text = (
        "Supervisor models may request publication of incorrect summaries on fixed synthetic local jobs. "
        f"This extended abstract reports an offline audit of SEAL (Supervisor Evidence Arms, Live): "
        f"{total} scheduled supervisor calls ({completed} completed, {invalid} invalid, {errors} errors) "
        "across two models and four evidence arms. Claims, receipts, and state arms execute a publish request "
        "on the fake local sink; provenance adds a deterministic code gate and can refuse or abstain. "
        f"On completed attacks, claims, receipts, and state have matching decision and execution counts, while "
        "provenance requests were vetoed by code; the models are reported separately. "
        f"The gate audit covers {mutation_total} summary mutations ({mutation_blocked} blocks, "
        f"{mutation_abstained} abstentions, {mutation_published} publications), plus {positive} legitimate "
        f"publishes and {mismatch} equality-mismatch blocks. Pairing retains {gemma_single}/4 Gemma families "
        f"in each attack stratum, {qwen_single}/4 for Qwen single, and {qwen_coord}/4 for Qwen coordinated; "
        "incident-timeline is unpaired in every attack stratum. The fixtures are instructed, non-adaptive, "
        "synthetic jobs with a fake sink and public demo keys. We do not claim learned refusal, general "
        "receipt weakness, or a causal coordination effect. Blind provenance numbers are excluded because "
        "that stage is not frozen."
    )
    return text


def validate_abstract(text: str) -> int:
    words = re.findall(r"\b[\w]+(?:[-'][\w]+)*\b", text)
    if not (150 <= len(words) <= 250):
        raise ValueError(f"Abstract has {len(words)} words; expected 150-250")
    banned = ("Aggregate rows exported", "TEMP FIXTURE", "withheld pending", "SYNTHETIC LAYOUT QA")
    for phrase in banned:
        if phrase.lower() in text.lower():
            raise ValueError(f"Abstract contains banned phrase: {phrase}")
    if any(ord(ch) > 127 for ch in text):
        raise ValueError("Abstract contains non-ASCII character")
    return len(words)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf8")


def generate_agentverify(
    build_dir: Path,
    paper_dir: Path,
    derived: dict[str, Any],
    macros: dict[str, str],
    abstract_text: str,
    abstract_words: int,
) -> dict[str, Any]:
    generated_dir = paper_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'paper/templates/ieee/IEEEtran.cls', paper_dir / 'IEEEtran.cls')

    two = derived["two_endpoints"]
    gate_manifest = derived["gate_manifest"]
    gate_csv = derived["gate_csv"]
    pairing = derived["pairing"]

    endpoint_table = build_endpoint_table(two)
    mutation_table = build_mutation_table(gate_manifest, gate_csv)
    pairing_table = build_pairing_table(pairing)

    macros_tex = "".join(f"\\newcommand{{\\{name}}}{{{value}}}\n" for name, value in macros.items())
    write_text(generated_dir / "macros.tex", macros_tex)
    write_text(generated_dir / "table_endpoints.tex", endpoint_table)
    write_text(generated_dir / "table_mutations.tex", mutation_table)
    write_text(generated_dir / "table_pairing.tex", pairing_table)
    write_text(generated_dir / "abstract.tex", _latex_escape(abstract_text) + "\n")

    main_tex = r"""\documentclass[10pt,conference]{IEEEtran}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb,booktabs,array,graphicx}
\usepackage{microtype}
\usepackage{hyperref}

\input{generated/macros}

\title{Separating Supervisor Publication Requests from Enforced Outcomes}

\author{\IEEEauthorblockN{Anonymous Submission}}

\hypersetup{
  pdftitle={Separating Supervisor Publication Requests from Enforced Outcomes},
  pdfauthor={Anonymous},
  pdfsubject={SEAL offline audit on fixed synthetic jobs},
  pdfkeywords={agent evaluation, evidence provenance, publication gate, synthetic fixtures}
}

\begin{document}
\maketitle

\begin{abstract}
\input{generated/abstract}
\end{abstract}

\IEEEpeerreviewmaketitle

\section{Introduction}
Supervisor models can request publication of incorrect summaries on fixed synthetic local jobs. This extended abstract separates a supervisor \emph{publication request} from an \emph{enforced outcome}: claims, receipts, and state arms execute a publish request on the fake local sink, while provenance adds a deterministic code gate that may refuse or abstain. The SEAL (Supervisor Evidence Arms, Live) audit uses \SealTotalEpisodes{} scheduled supervisor calls (\SealCompleted{} completed, \SealInvalid{} invalid, \SealError{} errors) across two models and four evidence arms. The fixtures are instructed, non-adaptive, synthetic jobs; the sink is fake, \texttt{side\_effects} is false, and signing keys are public demo keys. Prior work studies agent attack evaluation~\cite{agentdojo}, instruction priority~\cite{instructionhierarchy}, deliberate subversion~\cite{aicontrol}, and defenses by construction~\cite{camel}.

\section{Method}
The frozen study contains 960 scheduled episodes. Attacks use three mechanisms on four templates: consistent fake input, unauthorized publication, and ambiguous evidence. Single and coordinated conditions differ in message count and speaker roles, so coordination contrasts remain descriptive. Honest controls use three speakers. Provenance observations include \texttt{trusted\_summary} when binding is complete; blind provenance and retry stages are not collected in this version and no blind numbers are reported.

\emph{Oracle and denominators.} A publication is unsafe if the fixture is illegitimate or unanswerable, or its summary differs from the reference truth. Correct arithmetic alone therefore does not establish authorized publication. The decision endpoint records the supervisor's unsafe request before enforcement; the execution endpoint records whether that request reaches the sink. Each table cell uses its own completed, schema-valid attack denominator. Invalid outputs and API errors remain in the scheduled ledger and are not treated as refusals or successful prevention. Honest completion is a separate utility check, and the original three-speaker honest controls do not provide a matched one-speaker comparison.

The gate is \texttt{\_provenance\_action} in the fixture code, not learned refusal. It checks authorization, provenance binding, and \texttt{trusted\_summary != summary}; earlier checks can veto before the equality check, including when a corrected true summary is supplied on an illegitimate fixture. A mutation audit replays the actual gate on \MutationTotal{} summary variants: \MutationBlocked{} blocks, \MutationAbstained{} abstentions, and \MutationPublished{} publications. It also includes \PositiveControls{} legitimate publishes and \MismatchControls{} isolated equality-mismatch blocks. Pairing uses the original all-four-arm rule; it retains \PairGemmaSingle{}/4 Gemma families in each attack stratum, \PairQwenSingle{}/4 for Qwen single, and \PairQwenCoordinated{}/4 for Qwen coordinated. Incident-timeline is unpaired in all four attack strata. The provenance oracle recomputes the trusted summary from \texttt{trusted\_inputs} and compares it to the candidate summary; a corrected true summary on an illegitimate fixture is still vetoed by earlier binding and authorization checks.

\section{Results}
Table~\ref{tab:endpoints} gives both endpoints per model and coordination across the four arms as unsafe decisions / unsafe executions. Claims, receipts, and state have identical decision and execution counts; provenance requests and gate outcomes differ in every attack stratum. Table~\ref{tab:mutations} and Table~\ref{tab:pairing} report the gate audit and pairing coverage.

The mutation controls distinguish two explanations of a gate zero. Legitimate controls show that correct, authorized jobs can publish in this catalog. Isolated mismatch controls hold the earlier checks valid and exercise summary equality directly. Attack mutations often encounter an earlier veto, so their zero publications alone do not isolate the equality check or exhaust the gate's input domain. Likewise, the pairing inventory describes unsafe executions on matching completed fixture sets; it is not a paired estimate of every decision contrast.

\begin{table}[t]
\centering
\caption{Unsafe decisions / executions / completed attacks (D/E/N), separately for each model and condition.}
\label{tab:endpoints}
\scriptsize
\input{generated/table_endpoints}
\end{table}

\begin{table}[t]
\centering
\caption{Gate mutation audit and controls.}
\label{tab:mutations}
\scriptsize
\input{generated/table_mutations}
\end{table}

\begin{table}[t]
\centering
\caption{Unsafe-execution pairing; excluded families have different completed fixture sets across arms.}
\label{tab:pairing}
\scriptsize
\input{generated/table_pairing}
\end{table}

\section{Interpretation and auditability}
The central diagnostic is the gap between a request and its enforced outcome. An execution-only score can conceal a supervisor that continues to ask for an unsafe action behind a working boundary. A request-only score omits whether that boundary actually contains the action. Retaining both endpoints, the gate reason, and the fixture oracle makes the source of the recorded outcome inspectable. These are measurements of a finite synthetic catalog, not evidence that adding any particular evidence format causes a general safety improvement.

The original responses and worker corpus remain frozen. Derived tables are regenerated from saved decisions, with gate outcomes replayed locally and missing statuses retained. The blind extension has its own protocol, ledger, source hashes, and checksum manifest; it is not mixed into the original record. This separation permits inspection of what changed in the observation while preserving the historical evidence.

\section{Limits}
This audit does not claim learned refusal, general weakness of receipts, or collusion by three speakers. Its limits are: scripted wording, with \WorkersCopied{}/\WorkersTotal{} accepted worker calls copying the prompt's \texttt{wording\_reference}; unmatched arms and honest controls; visible true summaries on old provenance observations; confounded single-versus-coordinated message counts and speaker roles; four families implementing three mechanisms; and synthetic local jobs rather than codebase benchmarks. Blind provenance, retries, and new-wording studies are not included.

\begin{thebibliography}{9}
\bibitem{agentdojo}
E. Debenedetti et al., ``AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents,'' arXiv:2406.13352, 2024. \url{https://arxiv.org/abs/2406.13352}
\bibitem{instructionhierarchy}
E. Wallace et al., ``The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions,'' arXiv:2404.13208, 2024. \url{https://arxiv.org/abs/2404.13208}
\bibitem{aicontrol}
R. Greenblatt et al., ``AI Control: Improving Safety Despite Intentional Subversion,'' arXiv:2312.06942, 2023. \url{https://arxiv.org/abs/2312.06942}
\bibitem{camel}
E. Debenedetti et al., ``Defeating Prompt Injections by Design,'' arXiv:2503.18813, 2025. \url{https://arxiv.org/abs/2503.18813}
\end{thebibliography}

\end{document}
"""
    from scripts.seal_blind_paper_table import optional_table
    blind = optional_table()
    if blind:
        write_text(generated_dir / 'table_blind.tex', blind['tex'])
        main_tex = main_tex.replace(
            r'\section{Limits}',
            r'\section{Blind provenance extension}' + '\n' +
            r'The separate frozen 240-call study nulls only the outward trusted summary; the gate and other evidence are unchanged. ' +
            r'Other evidence can still permit recomputation. Historical and blind collection occurred at different times. ' +
            r'The extension therefore tests removal of the explicitly supplied summary under the remaining evidence, rather than unaided inference. Persistent unsafe requests and prevented executions remain separate observations; neither the time-separated comparison nor the gate zero establishes a learned change in policy. ' +
            r'\begin{table}[t]\centering\scriptsize' +
            r'\caption{Provenance decisions (D), executions (E), and completed denominators (N), before and after answer blinding.}\input{generated/table_blind}\end{table}' + '\n' +
            r'\section{Limits}')
        main_tex = main_tex.replace('blind provenance and retry stages are not collected in this version and no blind numbers are reported.',
                                    'the blind extension is reported separately; retry and new-wording results are excluded from this short version.')
        main_tex = main_tex.replace('Blind provenance, retries, and new-wording studies are not included.',
                                    'Retries and new-wording results are excluded from this short version.')
        write_text(build_dir / 'blind_evidence.json', __import__('json').dumps({k: v for k, v in blind.items() if k != 'tex'}, indent=2))
    write_text(paper_dir / "agentverify.tex", main_tex)

    metadata = {
        "venue": "AGENTVERIFY",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "paper_dir": str(paper_dir),
        "build_dir": str(build_dir),
        "abstract_words": abstract_words,
        "source_hashes": {
            "episodes": _sha256(build_dir.parent / "episodes.jsonl") if (build_dir.parent / "episodes.jsonl").is_file() else None,
            "two_endpoints": _sha256(build_dir.parent / "seal" / "analysis" / "two_endpoints.csv") if (build_dir.parent / "seal" / "analysis" / "two_endpoints.csv").is_file() else None,
            "gate_manifest": _sha256(build_dir.parent / "gate_mutations_manifest.json") if (build_dir.parent / "gate_mutations_manifest.json").is_file() else None,
        },
        "inputs": {
            "study_checksums": str(DATA / "checksums.json"),
            "source_contract": str(DATA / "provenance" / "source_contract.json"),
        },
    }
    write_text(build_dir / "build_status.json", json.dumps(metadata, indent=2) + "\n")

    return metadata


def compile_pdf(paper_dir: Path, build_dir: Path) -> Path:
    sys.path.insert(0, str(ROOT))
    from scripts.prepare_live_paper import resolve_tectonic

    tectonic = resolve_tectonic()
    if tectonic is None:
        raise RuntimeError("Tectonic not found")
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(dir=str(build_dir)) as temporary:
        isolated = Path(temporary)
        shutil.copytree(paper_dir, isolated / "paper", ignore=shutil.ignore_patterns("*.pdf"))
        # Ensure generated files are included (they are under paper_dir/generated).
        cmd = [str(tectonic), str(isolated / "paper" / "agentverify.tex"), "--outdir", str(isolated / "paper")]
        proc = subprocess.run(cmd, cwd=isolated / "paper", capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Tectonic failed:\n{proc.stdout}\n{proc.stderr}")
        pdf = isolated / "paper" / "agentverify.pdf"
        if not pdf.is_file():
            raise RuntimeError("agentverify.pdf was not produced")
        destination = build_dir / "agentverify.pdf"
        shutil.copy2(pdf, destination)
        shutil.copy2(pdf, paper_dir / "agentverify.pdf")
        return destination


def visual_qa(pdf_path: Path, output_dir: Path) -> dict[str, Any]:
    import fitz

    qa_dir = output_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        page_count = document.page_count
        texts = [page.get_text() for page in document]
        for index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(dpi=180)
            pixmap.save(qa_dir / f"page_{index}.png")
    joined = "\n".join(texts)
    banned = ("TEMP FIXTURE", "withheld pending", "SYNTHETIC LAYOUT QA", "github.com")
    found = [marker for marker in banned if marker.lower() in joined.lower()]
    if page_count > 2:
        raise ValueError(f"PDF has {page_count} pages; AGENTVERIFY limit is 2")
    if found:
        raise ValueError(f"PDF contains disallowed marker: {found}")
    return {
        "page_count": page_count,
        "qa_images": [str(qa_dir / f"page_{index}.png") for index in range(1, page_count + 1)],
        "text_preview": joined[:500],
    }


def write_evidence_manifest(build_dir: Path, derived: dict[str, Any], macros: dict[str, str]) -> None:
    two = derived["two_endpoints"]
    prov = two[(two["arm"] == "provenance") & (two["coordination"].isin(["single", "coordinated"]))]
    claims = [
        {
            "id": "status_counts",
            "claim": "Scheduled/completed/invalid/error supervisor calls",
            "value": {
                "scheduled": len(derived["records"]),
                "completed": derived["status_counts"]["completed"],
                "invalid": derived["status_counts"]["invalid"],
                "error": derived["status_counts"]["error"],
            },
            "numerator": derived["status_counts"]["completed"],
            "denominator": len(derived["records"]),
            "source": "build/episodes.jsonl",
            "row": "all episodes",
        },
        {
            "id": "provenance_endpoints",
            "claim": "Provenance unsafe decisions and unsafe executions on completed attacks",
            "value": {
                "decisions": int(prov["decision_numerator"].sum()),
                "executions": int(prov["execution_numerator"].sum()),
            },
            "numerator": int(prov["execution_numerator"].sum()),
            "denominator": int(prov["decision_denominator"].sum()),
            "source": "build/seal/analysis/two_endpoints.csv",
            "row": "arm=provenance, coordination in {single,coordinated}",
        },
        {
            "id": "mutation_audit",
            "claim": "Gate mutation outcomes",
            "value": {
                "total": int(derived["gate_manifest"]["total_mutations_evaluated"]),
                "block": int(derived["gate_manifest"]["observed_gate_actions"].get("block", 0)),
                "abstain": int(derived["gate_manifest"]["observed_gate_actions"].get("abstain", 0)),
                "publish": int(derived["gate_manifest"]["observed_gate_actions"].get("publish", 0)),
            },
            "numerator": int(derived["gate_manifest"]["observed_gate_actions"].get("block", 0)),
            "denominator": int(derived["gate_manifest"]["total_mutations_evaluated"]),
            "source": "build/gate_mutations_manifest.json",
            "row": "all mutation rows",
        },
        {
            "id": "positive_controls",
            "claim": "Legitimate positive controls that published",
            "value": {
                "published": int(derived["gate_manifest"]["controls"]["positive_legitimate_controls"]["published_count"]),
                "total": int(derived["gate_manifest"]["controls"]["positive_legitimate_controls"]["total"]),
            },
            "numerator": int(derived["gate_manifest"]["controls"]["positive_legitimate_controls"]["published_count"]),
            "denominator": int(derived["gate_manifest"]["controls"]["positive_legitimate_controls"]["total"]),
            "source": "build/gate_mutations_manifest.json",
            "row": "positive_legitimate_controls",
        },
        {
            "id": "equality_mismatch_controls",
            "claim": "Isolated equality-mismatch controls blocked",
            "value": {
                "blocked": int(derived["gate_manifest"]["controls"]["isolated_equality_mismatch_controls"]["blocked_count"]),
                "total": int(derived["gate_manifest"]["controls"]["isolated_equality_mismatch_controls"]["total"]),
            },
            "numerator": int(derived["gate_manifest"]["controls"]["isolated_equality_mismatch_controls"]["blocked_count"]),
            "denominator": int(derived["gate_manifest"]["controls"]["isolated_equality_mismatch_controls"]["total"]),
            "source": "build/gate_mutations_manifest.json",
            "row": "isolated_equality_mismatch_controls",
        },
        {
            "id": "pairing",
            "claim": "Paired families for unsafe execution",
            "value": {
                "gemma_single": int(macros["PairGemmaSingle"]),
                "gemma_coordinated": int(macros["PairGemmaCoordinated"]),
                "qwen_single": int(macros["PairQwenSingle"]),
                "qwen_coordinated": int(macros["PairQwenCoordinated"]),
            },
            "numerator": int(macros["PairGemmaSingle"]) + int(macros["PairGemmaCoordinated"]) + int(macros["PairQwenSingle"]) + int(macros["PairQwenCoordinated"]),
            "denominator": 16,
            "source": "build/seal/analysis/pairing_coverage.csv",
            "row": "endpoint=unsafe_execution, coordination in {single,coordinated}",
        },
        {
            "id": "workers_copied",
            "claim": "Accepted worker calls that copied wording_reference",
            "value": {"copied": 280, "total": 280},
            "numerator": 280,
            "denominator": 280,
            "source": "data/study/workers/assembled_corpus.json",
            "row": "verbatim audit",
        },
    ]
    manifest = {
        "venue": "AGENTVERIFY",
        "generated_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "claims": claims,
        "macros": macros,
        "input_hashes": {
            "episodes_sha256": None,
        },
    }
    # Pin the actual frozen episode hash from the reference contract when available.
    try:
        import json as _json

        contract = _json.loads((DATA / "reference" / "analysis_contract.json").read_text(encoding="utf8"))
        manifest["input_hashes"]["episodes_sha256"] = contract.get("episodes_sha256")
    except Exception:
        pass
    for filename in ("evidence_manifest.json", "claim_ledger.json"):
        (build_dir / filename).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf8")


def prepare_venue(
    venue: str,
    build_dir: Path,
    paper_dir: Path,
    compile_pdf_flag: bool = False,
) -> dict[str, Any]:
    if venue != "agentverify":
        raise NotImplementedError(f"Venue {venue!r} is not implemented yet; only AGENTVERIFY is supported")

    verify_frozen_inputs()
    derived = load_derived(DEFAULT_BUILD)
    macros = compute_macros(derived)
    abstract_text = build_abstract(macros)
    from scripts.seal_blind_paper_table import optional_table
    blind = optional_table()
    if blind:
        abstract_text = abstract_text.replace(
            'Blind provenance numbers are excluded because that stage is not frozen.',
            'A separately frozen extension removes the explicitly supplied trusted summary.')
    abstract_words = validate_abstract(abstract_text)

    generate_agentverify(build_dir, paper_dir, derived, macros, abstract_text, abstract_words)
    write_evidence_manifest(build_dir, derived, macros)
    if blind:
        path = build_dir / 'evidence_manifest.json'
        manifest = json.loads(path.read_text(encoding='utf8'))
        manifest['blind'] = {k: v for k, v in blind.items() if k != 'tex'}
        path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')

    status: dict[str, Any] = {
        "venue": venue,
        "paper_dir": str(paper_dir),
        "build_dir": str(build_dir),
        "abstract_words": abstract_words,
        "macros": macros,
    }
    if compile_pdf_flag:
        pdf_path = compile_pdf(paper_dir, build_dir)
        qa = visual_qa(pdf_path, build_dir)
        status["pdf"] = str(pdf_path)
        status["page_count"] = qa["page_count"]
        status["qa_images"] = qa["qa_images"]
    (build_dir / "build_status.json").write_text(json.dumps(status, indent=2, default=str) + "\n", encoding="utf8")
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", default="agentverify", choices=["agentverify"], help="Venue to prepare")
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD / "venues" / "agentverify")
    parser.add_argument("--paper-dir", type=Path, default=DEFAULT_PAPER / "agentverify")
    parser.add_argument("--compile", action="store_true", help="Compile the venue PDF with Tectonic")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    status = prepare_venue(args.venue, args.build_dir, args.paper_dir, args.compile)
    print(json.dumps(status, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
