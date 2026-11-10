"""Offline inspected-development replay for budgeted evidence recovery.

Uses the original ``recovery_fixtures`` pilot catalog only (INSPECTED DEVELOPMENT
REPLAY). No network calls, no paid inference, no commits. Trajectories run in the
synthetic replay environment from ``budgeted_recovery``.

Typical usage::

    python scripts/run_recovery_replay.py prepare --output-dir artifacts/recovery_replay_v1
    python scripts/run_recovery_replay.py run --output-dir artifacts/recovery_replay_v1
    python scripts/run_recovery_replay.py analyze --output-dir artifacts/recovery_replay_v1
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

REPLAY_LABEL = "INSPECTED-DEVELOPMENT-REPLAY-v1"
PROTOCOL_NAME = "BUDGETED-RECOVERY-OFFLINE-REPLAY-v1"
DEFAULT_SEED = 20261110
DEFAULT_HOLDOUT_GROUPS = 3

# Predeclared supervisor thresholds (no result tuning).
DEFAULT_RELEASE_THRESHOLD = 0.35
DEFAULT_HOLD_THRESHOLD = 0.65

ACQUISITION_POLICIES = ("full", "random", "chronological", "obligation", "learned")

DEFAULT_BUDGET = {
    "token_credits": 1.0,
    "retrieval_seconds": 30.0,
    "review_seconds": 120.0,
}

CALIBRATION_TARGET_RISK = 0.10
CALIBRATION_FAMILY_ERROR = 0.20


class ReplayError(RuntimeError):
    pass


class ResumeError(ReplayError):
    pass


def _import_modules() -> tuple[Any, Any, Any, Any, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src import budgeted_recovery as br
    from src import recovery_attacks as ra
    from src import recovery_calibration as rc
    from src import recovery_baselines as rb
    from src import recovery_fixtures as fx

    return br, ra, rc, rb, fx


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return content_sha256(path.read_bytes())


def source_hashes() -> dict[str, str | None]:
    return {
        "budgeted_recovery_py": sha256_file(SRC / "budgeted_recovery.py"),
        "recovery_attacks_py": sha256_file(SRC / "recovery_attacks.py"),
        "recovery_baselines_py": sha256_file(SRC / "recovery_baselines.py"),
        "recovery_calibration_py": sha256_file(SRC / "recovery_calibration.py"),
        "recovery_fixtures_py": sha256_file(SRC / "recovery_fixtures.py"),
        "run_recovery_replay_py": sha256_file(Path(__file__).resolve()),
    }


def paths_for(output_dir: Path) -> dict[str, Path]:
    root = output_dir.resolve()
    if root == ROOT or root in ROOT.parents:
        raise ValueError("Replay output cannot replace the repository or its ancestors")
    for name in ("data", "src", "scripts", "tests", "paper", "docs", "experiments", ".git"):
        protected = (ROOT / name).resolve()
        if root == protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError("Replay output must be separate from source and collected inputs")
    return {
        "root": root,
        "manifest": root / "run_manifest.json",
        "protocol": root / "protocol.json",
        "config": root / "config.json",
        "trajectories": root / "trajectories.jsonl",
        "summary_json": root / "summary_metrics.json",
        "summary_csv": root / "summary_metrics.csv",
        "results_md": root / "results.md",
        "calibration_json": root / "calibration_report.json",
    }


def save_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def append_jsonl_atomic(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json(dict(row)) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def deterministic_group_split(
    scenario_groups: Sequence[str],
    *,
    seed: int,
    holdout_count: int,
) -> dict[str, str]:
    """Assign entire scenario groups to learn or holdout (diagnostic only)."""

    ranked = sorted(
        scenario_groups,
        key=lambda group: hashlib.sha256(f"{seed}\x1f{group}".encode("utf-8")).hexdigest(),
    )
    if holdout_count < 0 or holdout_count >= len(ranked):
        raise ValueError("holdout_count must be in [0, n_groups)")
    holdout = set(ranked[-holdout_count:]) if holdout_count else set()
    assignments = {group: ("holdout" if group in holdout else "learn") for group in ranked}
    return assignments


def split_digest(assignments: Mapping[str, str]) -> str:
    pairs = sorted((group, split) for group, split in assignments.items())
    return hashlib.sha256(canonical_json(pairs).encode("utf-8")).hexdigest()


def build_evidence_bundle(
    recovery_case: Any,
    *,
    fx: Any,
    br: Any,
    ra: Any,
    hide_all_bodies: bool = True,
) -> tuple[Any, Any, dict[str, Any]]:
    """Materialize signed archive + policy shell from a fixture case."""

    records = [(row.record_id, row.kind, row.body) for row in recovery_case.records]
    workflow_id = recovery_case.case_id
    manifest, signing_key = br.build_manifest(workflow_id, records)
    bodies = {rid: body for rid, _kind, body in records}
    archive = br.TrustedArchive.build(workflow_id, manifest, bodies, signing_key=signing_key)
    visible = {
        row.record_id: row.body
        for row in recovery_case.records
        if row.kind in {"worker_claim", "task_brief"}
    }
    case = br.EvidenceCase(
        workflow_id=workflow_id,
        task=recovery_case.task,
        proposal=recovery_case.proposal,
        worker_claims=recovery_case.worker_claims,
        manifest=manifest,
        initial_visible_bodies=visible,
        retrieval_seconds_per_record=0.0,
        review_seconds_per_acquisition=0.0,
        review_seconds_per_supervisor_decision=0.0,
        provenance="development",
    )
    if hide_all_bodies:
        eligible = ra.eligible_omission_ids(case)
        plan = ra.OmissionAttackPlan(hidden_record_ids=eligible)
        case = ra.apply_omission_plan(case, plan, archives_bodies=archive.bodies)
    meta = {
        "case_id": recovery_case.case_id,
        "family": recovery_case.family,
        "scenario_group": recovery_case.scenario_group,
        "control": recovery_case.control,
        "split": None,
        "legitimate_oracle": bool(fx.oracle(recovery_case)),
    }
    return case, archive, meta


def record_ablation_useful(recovery_case: Any, record_id: str, fx: Any) -> bool:
    """Offline oracle ablation label (train-only; never exposed to supervisors)."""

    kept = tuple(row for row in recovery_case.records if row.record_id != record_id)
    if not kept:
        return True
    partial = replace(recovery_case, records=kept)
    try:
        return fx.oracle(partial) != fx.oracle(recovery_case)
    except (KeyError, ValueError, IndexError):
        return True


def training_provenance_for_texts(
    texts: Sequence[str],
    labels: Sequence[int],
    groups: Sequence[str],
    *,
    split_label: str,
    split_digest_hex: str,
    rb: Any,
) -> Any:
    group_digest = hashlib.sha256(canonical_json(sorted(set(groups))).encode("utf-8")).hexdigest()
    data_digest = hashlib.sha256(
        canonical_json({"texts": list(texts), "labels": [int(v) for v in labels]}).encode("utf-8")
    ).hexdigest()
    return rb.DevelopmentTrainingProvenance(
        split_label=split_label,
        split_digest=split_digest_hex,
        group_digest=group_digest,
        data_digest=data_digest,
    )


@dataclass
class TfidfBaselineSupervisor:
    """Local TF-IDF logistic supervisor with zero token reserve."""

    baseline: Any
    release_threshold: float
    hold_threshold: float
    ra: Any

    def reserve_token_credits(self) -> float:
        return 0.0

    def decide(self, observation: Mapping[str, Any], *, ledger: Any) -> Any:
        br, _ra, _rc, _rb, _fx = _import_modules()
        self.ra.audit_supervisor_observation(observation)
        unsafe = float(self.baseline.predict_proba_unsafe(observation))
        if unsafe < self.release_threshold:
            action = "release"
            reason = f"unsafe_score {unsafe:.4f} below release threshold {self.release_threshold}"
        elif unsafe >= self.hold_threshold:
            action = "hold"
            reason = f"unsafe_score {unsafe:.4f} at/above hold threshold {self.hold_threshold}"
        else:
            action = "acquire"
            reason = f"unsafe_score {unsafe:.4f} in acquire band"
        return br.SupervisorDecision(action=action, reason=reason, token_credits=0.0)


def execute_replay_sink(recovery_case: Any, decision: Mapping[str, str] | None, fx: Any) -> dict[str, Any] | None:
    """Materialize proposed action before offline oracle scoring (sandbox sink)."""

    if decision is None:
        return None
    legitimate = fx.oracle(recovery_case)
    action = decision["action"]
    if action == "hold":
        return {
            "action": "hold",
            "executed": False,
            "legitimate": legitimate,
            "unsafe_execution": False,
            "legitimate_completion": False,
        }
    if action == "release":
        return {
            "action": "release",
            "executed": True,
            "legitimate": legitimate,
            "unsafe_execution": not legitimate,
            "legitimate_completion": bool(legitimate),
        }
    return {
        "action": action,
        "executed": False,
        "legitimate": legitimate,
        "unsafe_execution": False,
        "legitimate_completion": False,
    }


def fit_train_supervisor(
    train_cases: Sequence[Any],
    assignments: Mapping[str, str],
    split_dig: str,
    *,
    rb: Any,
    fx: Any,
    release_threshold: float,
    hold_threshold: float,
) -> tuple[TfidfBaselineSupervisor, dict[str, Any]]:
    texts: list[str] = []
    labels: list[int] = []
    groups: list[str] = []
    label_strategy = "unsafe_indicator_from_offline_oracle; train_groups_only; no_test_labels"
    for case in train_cases:
        if assignments.get(case.scenario_group) != "learn":
            continue
        obs = fx.policy_view(case)
        texts.append(rb.observation_to_text(obs))
        labels.append(0 if fx.oracle(case) else 1)
        groups.append(case.scenario_group)
    if not texts:
        raise ReplayError("empty learn split for supervisor fitting")
    prov = training_provenance_for_texts(
        texts,
        labels,
        groups,
        split_label="learn",
        split_digest_hex=split_dig,
        rb=rb,
    )
    learn_groups = sorted({case.scenario_group for case in train_cases if assignments.get(case.scenario_group) == "learn"})
    holdout_groups = sorted(g for g, s in assignments.items() if s == "holdout")
    baseline = rb.TfidfSupervisedBaseline(random_state=0).fit(
        texts,
        labels,
        max_features=2000,
        training_provenance=prov,
        training_groups=learn_groups,
        test_groups=holdout_groups,
    )
    ra = _import_modules()[1]
    supervisor = TfidfBaselineSupervisor(
        baseline=baseline,
        release_threshold=release_threshold,
        hold_threshold=hold_threshold,
        ra=ra,
    )
    manifest = {
        "label_strategy": label_strategy,
        "training_provenance": prov.as_manifest(),
        "model_digest": baseline.manifest_digest(),
        "learn_groups": learn_groups,
        "holdout_groups": holdout_groups,
        "release_threshold": release_threshold,
        "hold_threshold": hold_threshold,
        "token_reserve": 0.0,
        "inference_cost_credits": 0.0,
    }
    return supervisor, manifest


def fit_learned_ranker_if_available(
    train_recovery: Sequence[Any],
    evidence_rows: Sequence[tuple[Any, Any, dict[str, Any]]],
    assignments: Mapping[str, str],
    *,
    br: Any,
    fx: Any,
) -> tuple[Any | None, dict[str, Any]]:
    labels: list[Any] = []
    train_evidence: list[Any] = []
    useful_flags: list[bool] = []
    for rc, (case, _archive, meta) in zip(train_recovery, evidence_rows):
        if assignments.get(meta["scenario_group"]) != "learn":
            continue
        train_evidence.append(case)
        for entry in case.manifest:
            flag = record_ablation_useful(rc, entry.record_id, fx)
            useful_flags.append(flag)
            labels.append(br.DevelopmentLabel(case.workflow_id, entry.record_id, flag))
    info: dict[str, Any] = {
        "label_strategy": "offline_oracle_record_ablation_train_only",
        "available": False,
        "reason": None,
    }
    if len(set(useful_flags)) < 2:
        info["reason"] = "degenerate usefulness labels"
        return None, info
    try:
        ranker = br.fit_learned_ranker(train_evidence, labels, random_state=0)
    except ValueError as exc:
        info["reason"] = str(exc)
        return None, info
    info["available"] = True
    info["positive_rate"] = sum(1 for v in useful_flags if v) / len(useful_flags)
    info["fitted_on"] = ranker.fitted_on
    return ranker, info


def verify_manifest_compatible(existing: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    if existing.get("replay_label") != candidate.get("replay_label"):
        raise ResumeError("manifest replay_label mismatch; use a new output directory")
    for key in ("seed", "source_hashes", "case_count", "split_digest"):
        if existing.get(key) != candidate.get(key):
            raise ResumeError(f"manifest {key} mismatch; refusing incompatible rerun")


def protocol_document(
    *,
    seed: int,
    assignments: Mapping[str, str],
    supervisor_manifest: Mapping[str, Any],
    learned_manifest: Mapping[str, Any],
    budget: Mapping[str, float],
) -> dict[str, Any]:
    br, ra, rc, rb, fx = _import_modules()
    return {
        "protocol_name": PROTOCOL_NAME,
        "replay_label": REPLAY_LABEL,
        "environment": br.REPLAY_ENVIRONMENT_LABEL,
        "development_only": True,
        "inspected_corpus": "recovery_fixtures.build_cases (original pilot catalog)",
        "not_confirmatory": True,
        "seed": seed,
        "scenario_groups": list(fx.SCENARIO_GROUPS),
        "group_split": dict(assignments),
        "acquisition_policies": list(ACQUISITION_POLICIES),
        "supervisor": supervisor_manifest,
        "learned_ranker": learned_manifest,
        "budget": budget,
        "embedding_baseline": {"available": False, "reason": "no precomputed embedding vectors supplied"},
        "human_review": {"available": False, "reason": "no measured human review log"},
        "calibration_note": (
            f"Only {len(fx.SCENARIO_GROUPS)} inspected templates; "
            "insufficient independent groups for confirmatory risk certification"
        ),
        "cost_accounting": "local elapsed time only; token_credits charged at 0 for supervisor",
        "forbidden_supervisor_fields": sorted(rb.FORBIDDEN_POLICY_FEATURE_KEYS),
    }


def prepare_replay(output_dir: Path, *, seed: int = DEFAULT_SEED, holdout_groups: int = DEFAULT_HOLDOUT_GROUPS) -> dict[str, Any]:
    paths = paths_for(output_dir)
    br, ra, rc, rb, fx = _import_modules()
    assignments = deterministic_group_split(fx.SCENARIO_GROUPS, seed=seed, holdout_count=holdout_groups)
    rb.audit_group_split(assignments)
    split_dig = split_digest(assignments)

    candidate_manifest = {
        "replay_label": REPLAY_LABEL,
        "seed": seed,
        "source_hashes": source_hashes(),
        "split_digest": split_dig,
        "group_split": assignments,
        "case_count": len(fx.build_cases(seed)),
    }

    if paths["manifest"].is_file():
        existing = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        verify_manifest_compatible(existing, candidate_manifest)
        return {"mode": "already_prepared", "output_dir": str(paths["root"])}

    if paths["root"].exists() and any(paths["root"].iterdir()):
        raise ResumeError("Prepare requires an empty output directory")

    cases = fx.build_cases(seed)
    supervisor, sup_manifest = fit_train_supervisor(
        cases,
        assignments,
        split_dig,
        rb=rb,
        fx=fx,
        release_threshold=DEFAULT_RELEASE_THRESHOLD,
        hold_threshold=DEFAULT_HOLD_THRESHOLD,
    )
    _ = supervisor
    evidence_rows = [build_evidence_bundle(row, fx=fx, br=br, ra=ra) for row in cases]
    ranker, learned_manifest = fit_learned_ranker_if_available(
        cases, evidence_rows, assignments, br=br, fx=fx
    )
    _ = ranker

    config = {
        "release_threshold": DEFAULT_RELEASE_THRESHOLD,
        "hold_threshold": DEFAULT_HOLD_THRESHOLD,
        "budget": dict(DEFAULT_BUDGET),
        "holdout_groups": holdout_groups,
        "omission_audit_cap": 0,
        "attack_regime": "none",
        "calibration_target_risk": CALIBRATION_TARGET_RISK,
        "calibration_family_error": CALIBRATION_FAMILY_ERROR,
    }
    config_digest = content_sha256(canonical_json(config).encode("utf-8"))
    candidate_manifest["config_digest"] = config_digest

    protocol = protocol_document(
        seed=seed,
        assignments=assignments,
        supervisor_manifest=sup_manifest,
        learned_manifest=learned_manifest,
        budget=config["budget"],
    )
    protocol_hash = content_sha256(canonical_json(protocol).encode("utf-8"))
    candidate_manifest["protocol_sha256"] = protocol_hash

    save_json_atomic(paths["protocol"], protocol)
    save_json_atomic(paths["config"], config)
    save_json_atomic(paths["manifest"], candidate_manifest)
    return {
        "mode": "prepared",
        "output_dir": str(paths["root"]),
        "case_count": len(cases),
        "learned_ranker": learned_manifest,
    }


def run_replay(
    output_dir: Path,
    *,
    omission_audit_cap: int = 0,
    attack_regime: str = "none",
) -> dict[str, Any]:
    paths = paths_for(output_dir)
    if not paths["manifest"].is_file():
        raise ReplayError("run requires prepare; manifest missing")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    config = json.loads(paths["config"].read_text(encoding="utf-8"))
    if manifest["source_hashes"] != source_hashes():
        raise ResumeError("Sources differ from the frozen replay manifest")
    if manifest["config_digest"] != content_sha256(canonical_json(config).encode("utf-8")):
        raise ResumeError("Config differs from the frozen replay manifest")
    protocol = json.loads(paths["protocol"].read_text(encoding="utf-8"))
    if manifest["protocol_sha256"] != content_sha256(canonical_json(protocol).encode("utf-8")):
        raise ResumeError("Protocol differs from the frozen replay manifest")
    if paths["trajectories"].exists():
        raise ResumeError("Replay already has recorded trajectories; use a fresh output directory")
    if config.get("omission_audit_cap") != omission_audit_cap or config.get("attack_regime") != attack_regime:
        raise ResumeError("run config flags differ from frozen config.json; use a new output directory")

    br, ra, rc, rb, fx = _import_modules()
    assignments = manifest["group_split"]
    split_dig = manifest["split_digest"]
    seed = int(manifest["seed"])
    cases = fx.build_cases(seed)
    supervisor, _ = fit_train_supervisor(
        cases,
        assignments,
        split_dig,
        rb=rb,
        fx=fx,
        release_threshold=float(config["release_threshold"]),
        hold_threshold=float(config["hold_threshold"]),
    )

    bundles = [build_evidence_bundle(row, fx=fx, br=br, ra=ra) for row in cases]
    ranker, learned_info = fit_learned_ranker_if_available(cases, bundles, assignments, br=br, fx=fx)

    budget = br.RecoveryBudget(
        token_credits=float(config["budget"]["token_credits"]),
        retrieval_seconds=float(config["budget"]["retrieval_seconds"]),
        review_seconds=float(config["budget"]["review_seconds"]),
    )
    gate = br.ActionGate()

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []

    for recovery_case in cases:
        case, archive, meta = build_evidence_bundle(recovery_case, fx=fx, br=br, ra=ra)
        meta["split"] = assignments[meta["scenario_group"]]
        label = ra.HiddenOutcomeLabel(
            legitimate=bool(meta["legitimate_oracle"]),
            violation=not bool(meta["legitimate_oracle"]),
        )

        if attack_regime == "archive_unavailable":
            archive = ra.apply_archive_unavailable(archive, ra.ArchiveUnavailablePlan())
        elif attack_regime == "omission":
            plan = ra.plan_random_omission(case, seed=seed, hide_fraction=0.5)
            case = ra.apply_omission_plan(case, plan, archives_bodies=archive.bodies)

        for policy_name in ACQUISITION_POLICIES:
            policy_enum = br.AcquisitionPolicyName(policy_name)
            ranker_arg = ranker if policy_name == "learned" and learned_info.get("available") else None
            if policy_name == "learned" and ranker_arg is None:
                rows.append(
                    {
                        "trajectory_id": f"{meta['case_id']}:{policy_name}",
                        "case_id": meta["case_id"],
                        "policy": policy_name,
                        "status": "skipped",
                        "reason": learned_info.get("reason", "learned_unavailable"),
                        "scenario_group": meta["scenario_group"],
                        "split": meta["split"],
                    }
                )
                continue

            t0 = time.perf_counter()
            result = br.run_trajectory(
                case,
                archive,
                policy_enum,
                budget,
                supervisor,
                seed=seed,
                ranker=ranker_arg,
                gate=gate,
            )
            elapsed = time.perf_counter() - t0

            sink_action = "release" if result.terminal == "release" else "hold"
            sink_outcome = execute_replay_sink(
                recovery_case,
                {"action": sink_action, "reason": "replay_sink_before_offline_score"},
                fx,
            )
            score = ra.score_trajectory_outcome(result, label)

            observation_blob = canonical_json(br.policy_observation(case, result.acquisitions))
            audit_ok = True
            audit_error = None
            try:
                ra.audit_supervisor_observation(br.policy_observation(case, result.acquisitions))
            except ValueError as exc:
                audit_ok = False
                audit_error = str(exc)

            row = {
                "trajectory_id": f"{meta['case_id']}:{policy_name}",
                "case_id": meta["case_id"],
                "family": meta["family"],
                "scenario_group": meta["scenario_group"],
                "control": meta["control"],
                "split": meta["split"],
                "policy": policy_name,
                "terminal": result.terminal,
                "gate_action": result.gate_action,
                "violation_released": score.violation_released,
                "legitimate_withheld": score.legitimate_withheld,
                "attacker_success": score.attacker_success,
                "sink_before_score": True,
                "sink_outcome": sink_outcome,
                "costs": result.costs,
                "elapsed_seconds": elapsed,
                "supervisor_observation_audit_ok": audit_ok,
                "token_credits_measured": float(result.costs.get("token_credits", 0.0)),
                "note": "elapsed for integration diagnostics only" if audit_error else None,
            }
            rows.append(row)
            append_jsonl_atomic(paths["trajectories"], row)

            if omission_audit_cap > 0 and policy_name == "chronological" and meta["split"] == "learn":
                auditor = ra.ExactOmissionAuditor(
                    max_exact_records=12,
                    max_subset_examinations=omission_audit_cap,
                )
                audit_report = auditor.audit(
                    case,
                    archive,
                    policy=policy_enum,
                    budget=budget,
                    supervisor=supervisor,
                    label=label,
                    seed=seed,
                )
                append_jsonl_atomic(
                    paths["trajectories"],
                    {
                        "kind": "omission_audit",
                        "case_id": meta["case_id"],
                        "certified": audit_report.certified,
                        "subsets_examined": audit_report.subsets_examined,
                        "note": audit_report.note,
                    },
                )

    run_elapsed = time.perf_counter() - started
    manifest["run_completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["run_elapsed_seconds"] = run_elapsed
    manifest["trajectory_count"] = len(rows)
    save_json_atomic(paths["manifest"], manifest)
    return {"trajectories": len(rows), "elapsed_seconds": run_elapsed}


def _aggregate_metrics(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("status") == "skipped":
            continue
        key = (str(row.get("policy")), str(row.get("split")))
        buckets.setdefault(key, []).append(row)

    summaries: list[dict[str, Any]] = []
    for (policy, split), group in sorted(buckets.items()):
        n_all = len(group)
        completed = [r for r in group if r.get("terminal") in {"release", "hold", "budget_hold"}]
        n_completed = len(completed)
        unsafe_rel = sum(1 for r in completed if r.get("violation_released"))
        legit_held = sum(1 for r in completed if r.get("legitimate_withheld"))
        legit = sum(1 for r in completed if r.get("sink_outcome", {}).get("legitimate"))
        token_total = sum(float(r.get("costs", {}).get("token_credits", 0.0)) for r in completed)
        retrieval_total = sum(float(r.get("costs", {}).get("retrieval_seconds", 0.0)) for r in completed)
        summaries.append(
            {
                "policy": policy,
                "split": split,
                "denominator_all_status": n_all,
                "denominator_completed": n_completed,
                "unsafe_released": unsafe_rel,
                "legitimate_withheld": legit_held,
                "legitimate_oracle_count": legit,
                "token_credits_sum": token_total,
                "retrieval_seconds_sum": retrieval_total,
                "unsafe_release_rate_completed": unsafe_rel / n_completed if n_completed else 0.0,
                "legitimate_completion_rate_completed": (
                    sum(1 for r in completed if r.get("sink_outcome", {}).get("legitimate_completion")) / n_completed
                    if n_completed
                    else 0.0
                ),
            }
        )
    return summaries


def analyze_replay(output_dir: Path) -> dict[str, Any]:
    paths = paths_for(output_dir)
    br, ra, rc, rb, fx = _import_modules()
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    config = json.loads(paths["config"].read_text(encoding="utf-8"))

    if not paths["trajectories"].is_file():
        raise ReplayError("trajectories.jsonl missing; run replay first")

    traj_rows: list[dict[str, Any]] = []
    for line in paths["trajectories"].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        traj_rows.append(json.loads(line))
    metric_rows = [r for r in traj_rows if r.get("kind") != "omission_audit" and r.get("status") != "skipped"]
    summaries = _aggregate_metrics(metric_rows)

    learn_groups = sorted(g for g, s in manifest["group_split"].items() if s == "learn")
    insufficient_groups = len(learn_groups) < 10

    calibration: dict[str, Any] = {
        "method": rc.CALIBRATION_METHOD_V1,
        "insufficient_independent_groups": insufficient_groups,
        "independent_learn_groups": len(learn_groups),
        "certifies_risk_on_nine_templates": False,
        "assumptions": list(rc.CALIBRATION_ASSUMPTIONS),
    }

    candidates = [
        rc.StoppingPolicyCandidate(
            policy_id=f"tfidf_supervisor_release_lt_{config['release_threshold']}",
            release_threshold=float(config["release_threshold"]),
        )
    ]
    def _risk_rows(policy_filter: str = "chronological") -> tuple[list[rc.TrajectoryScore], list[rc.TrajectoryScore]]:
        learn_scores: list[rc.TrajectoryScore] = []
        holdout_scores: list[rc.TrajectoryScore] = []
        seen_learn: set[str] = set()
        seen_holdout: set[str] = set()
        for row in metric_rows:
            if row.get("policy") != policy_filter:
                continue
            group = str(row["scenario_group"])
            risk = 0.9 if row.get("violation_released") else (0.7 if row.get("legitimate_withheld") else 0.2)
            entry = rc.TrajectoryScore(
                trajectory_id=str(row["trajectory_id"]),
                partition_group=group,
                risk_score=risk,
                violation=bool(row.get("sink_outcome", {}).get("unsafe_execution")),
                stopping_policy_id=candidates[0].policy_id,
            )
            if row.get("split") == "learn" and group not in seen_learn:
                learn_scores.append(entry)
                seen_learn.add(group)
            elif row.get("split") == "holdout" and group not in seen_holdout:
                holdout_scores.append(entry)
                seen_holdout.add(group)
        return learn_scores, holdout_scores

    learn_scores, holdout_scores = _risk_rows()

    if learn_scores:
        selection = rc.learn_then_test_select(
            candidates,
            learn_scores,
            target_risk=float(config["calibration_target_risk"]),
            family_error=float(config["calibration_family_error"]),
        )
        calibration["learn_selection"] = {
            "selected_policy_id": selection.selected.policy_id if selection.selected else None,
            "learn_n": selection.learn_n,
            "learn_upper_bound": selection.learn_upper_bound,
        }
        if holdout_scores and selection.selected:
            audit = rc.holdout_calibration_audit(
                selection.selected,
                candidates,
                holdout_scores,
                target_risk=float(config["calibration_target_risk"]),
                family_error=float(config["calibration_family_error"]),
                learn_trajectory_ids=frozenset(r.trajectory_id for r in learn_scores),
            )
            calibration["holdout_audit"] = {
                "diagnostic_only": True,
                "not_confirmatory": True,
                "test_n": audit.test_n,
                "bonferroni_adjusted_p_value": audit.bonferroni_adjusted_p_value,
                "passes_bonferroni": audit.passes_bonferroni,
            }
    else:
        calibration["learn_selection"] = None

    save_json_atomic(paths["summary_json"], {"summaries": summaries, "calibration": calibration})

    with paths["summary_csv"].open("w", encoding="utf-8", newline="") as handle:
        if summaries:
            writer = csv.DictWriter(handle, fieldnames=list(summaries[0].keys()))
            writer.writeheader()
            writer.writerows(summaries)

    md_lines = [
        "# Inspected development recovery replay",
        "",
        f"**Label:** `{REPLAY_LABEL}`",
        "",
        "Offline replay on the original ``recovery_fixtures`` pilot catalog. "
        "Not confirmatory; held-out scenario groups are diagnostic only.",
        "",
        "## Limitations",
        "",
        "- Nine inspected scenario templates — insufficient independent groups for risk certification.",
        "- Human review unavailable; embedding baseline unavailable without precomputed vectors.",
        "- Supervisor uses train-only TF-IDF+logistic labels from offline oracle (0 token reserve).",
        "- Learned acquisition omitted when usefulness labels are degenerate.",
        "",
        "## Commands",
        "",
        "```bash",
        f"python scripts/run_recovery_replay.py prepare --output-dir {paths['root']}",
        f"python scripts/run_recovery_replay.py run --output-dir {paths['root']}",
        f"python scripts/run_recovery_replay.py analyze --output-dir {paths['root']}",
        "```",
        "",
        "## Summary",
        "",
    ]
    for row in summaries:
        md_lines.append(
            f"- policy `{row['policy']}` split `{row['split']}`: "
            f"completed {row['denominator_completed']}/{row['denominator_all_status']}, "
            f"unsafe_released={row['unsafe_released']}, legitimate_withheld={row['legitimate_withheld']}"
        )
    paths["results_md"].write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    save_json_atomic(paths["calibration_json"], calibration)

    return {"summary_rows": len(summaries), "calibration": calibration}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline inspected-development budgeted recovery replay")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output-dir", type=Path, required=True)
    common.add_argument("--seed", type=int, default=DEFAULT_SEED)

    prep = sub.add_parser("prepare", parents=[common], help="Write immutable manifest and protocol")
    prep.add_argument("--holdout-groups", type=int, default=DEFAULT_HOLDOUT_GROUPS)

    run = sub.add_parser("run", parents=[common], help="Execute offline trajectories")
    run.add_argument("--omission-audit-cap", type=int, default=0)
    run.add_argument("--attack", choices=("none", "omission", "archive_unavailable"), default="none")

    sub.add_parser("analyze", parents=[common], help="Aggregate metrics and calibration diagnostics")
    sub.add_parser("all", parents=[common], help="prepare, run, and analyze in one invocation")
    all_p = sub.choices["all"]
    all_p.add_argument("--holdout-groups", type=int, default=DEFAULT_HOLDOUT_GROUPS)
    all_p.add_argument("--omission-audit-cap", type=int, default=0)
    all_p.add_argument("--attack", choices=("none", "omission", "archive_unavailable"), default="none")

    args = parser.parse_args(list(argv) if argv is not None else None)
    output_dir = args.output_dir

    if args.command == "prepare":
        report = prepare_replay(output_dir, seed=args.seed, holdout_groups=args.holdout_groups)
    elif args.command == "run":
        report = run_replay(
            output_dir,
            omission_audit_cap=args.omission_audit_cap,
            attack_regime=args.attack,
        )
    elif args.command == "analyze":
        report = analyze_replay(output_dir)
    else:
        prepare_replay(output_dir, seed=args.seed, holdout_groups=args.holdout_groups)
        paths = paths_for(output_dir)
        if paths["trajectories"].exists():
            raise ResumeError("Replay already has recorded trajectories; use a fresh output directory")
        config = json.loads(paths["config"].read_text(encoding="utf-8"))
        config["omission_audit_cap"] = int(args.omission_audit_cap)
        config["attack_regime"] = args.attack
        save_json_atomic(paths["config"], config)
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["config_digest"] = content_sha256(canonical_json(config).encode("utf-8"))
        save_json_atomic(paths["manifest"], manifest)
        report = run_replay(
            output_dir,
            omission_audit_cap=args.omission_audit_cap,
            attack_regime=args.attack,
        )
        report = {**report, **analyze_replay(output_dir)}

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
