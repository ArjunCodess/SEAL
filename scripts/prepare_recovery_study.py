"""Prepare-only manifest for the prospective budgeted recovery study (no API calls)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
FROZEN_ROOTS = (
    ROOT / "data",
    ROOT / "experiments",
    SRC,
    ROOT / "scripts",
    ROOT / "tests",
)


def _ensure_import_path() -> None:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _content_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def assert_safe_output_dir(output_dir: Path, *, allow_existing_nonempty: bool = False) -> Path:
    """Refuse outputs that overwrite frozen sources, repo ancestors, or symlink targets."""
    resolved = output_dir.resolve()
    if resolved == ROOT or ROOT.is_relative_to(resolved):
        raise ValueError("prepare output cannot replace the repository or its ancestors")
    for name in ("data", "src", "scripts", "tests", "paper", "docs", ".git", "experiments"):
        protected = (ROOT / name).resolve()
        if resolved == protected or resolved.is_relative_to(protected) or protected.is_relative_to(resolved):
            raise ValueError("prepare output must not overlap frozen inputs, sources, or experiments")
    for frozen in FROZEN_ROOTS:
        frozen_resolved = frozen.resolve()
        if resolved == frozen_resolved or resolved.is_relative_to(frozen_resolved):
            raise ValueError(f"prepare output cannot write inside frozen path {frozen}")
        if frozen_resolved.is_symlink() and resolved.is_relative_to(frozen_resolved.resolve()):
            raise ValueError("prepare output cannot write through a frozen symlink target")
    if output_dir.exists():
        if output_dir.is_symlink():
            raise ValueError("prepare output path cannot be a symlink")
        if any(output_dir.iterdir()) and not allow_existing_nonempty:
            raise RuntimeError("prepare requires an empty output directory")
    return resolved


def save_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(tmp, flags, 0o644)
    except FileExistsError:
        raise RuntimeError(f"refusing to clobber in-flight partial write at {tmp}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def source_hashes() -> dict[str, str | None]:
    from src.recovery_population import sha256_file

    docs = ROOT / "docs"
    return {
        "recovery_population_py": sha256_file(SRC / "recovery_population.py"),
        "prepare_recovery_study_py": sha256_file(Path(__file__).resolve()),
        "recovery_fixtures_py": sha256_file(SRC / "recovery_fixtures.py"),
        "budgeted_recovery_py": sha256_file(SRC / "budgeted_recovery.py"),
        "budgeted_recovery_protocol_md": sha256_file(docs / "budgeted-recovery-protocol.md"),
        "plan_audit_md": sha256_file(docs / "plan-audit.md"),
    }


def _tuple_ids(raw: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{field} must be a non-empty list of case_id strings")
    out = tuple(str(item) for item in raw)
    if len(set(out)) != len(out):
        raise ValueError(f"duplicate ids in {field}")
    return out


def load_observed_baselines(path: Path) -> list[Any]:
    from src.recovery_population import ObservedGroupBaseline

    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("observed baselines file must be a JSON list")
    out: list[ObservedGroupBaseline] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each observed baseline must be an object")
        out.append(
            ObservedGroupBaseline(
                scenario_group=str(row["scenario_group"]),
                violation_case_ids=_tuple_ids(row["violation_case_ids"], field="violation_case_ids"),
                legitimate_case_ids=_tuple_ids(row["legitimate_case_ids"], field="legitimate_case_ids"),
                baseline_unsafe_release_rate=float(row["baseline_unsafe_release_rate"]),
                baseline_legitimate_completion_rate=float(row["baseline_legitimate_completion_rate"]),
                provenance=str(row.get("provenance", "observed_development_comparator")),
            )
        )
    return out


def load_policy_contrasts(path: Path) -> list[Any]:
    from src.recovery_population import DevelopmentPolicyContrast

    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("policy contrasts file must be a JSON list")
    out: list[DevelopmentPolicyContrast] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each policy contrast must be an object")
        out.append(
            DevelopmentPolicyContrast(
                scenario_group=str(row["scenario_group"]),
                violation_case_ids=_tuple_ids(row["violation_case_ids"], field="violation_case_ids"),
                legitimate_case_ids=_tuple_ids(row["legitimate_case_ids"], field="legitimate_case_ids"),
                candidate_unsafe_release_rate=float(row["candidate_unsafe_release_rate"]),
                candidate_legitimate_completion_rate=float(row["candidate_legitimate_completion_rate"]),
                provenance=str(row.get("provenance", "candidate_policy_fit")),
            )
        )
    return out


def prepare_study(
    output_dir: Path,
    *,
    generator_seed: int,
    split_seed: int,
    variants_per_group: int,
    effect_spec: dict[str, float] | None,
    observed_baselines_path: Path | None,
    policy_contrasts_path: Path | None,
    simulations: int,
    max_independent_groups: int,
    sizing_rng_seed: int,
) -> dict[str, Any]:
    _ensure_import_path()
    from src import budgeted_recovery
    from src.recovery_population import (
        ConfirmatoryEffectSpec,
        ConfirmatorySizingResult,
        assign_splits,
        assert_population_oracle_contract,
        audit_split_leakage,
        build_population,
        build_prepare_manifest,
        canonical_json,
        development_paired_summaries,
        live_worker_record_schema,
        simulate_confirmatory_sizing,
        FRESH_SCENARIO_GROUPS,
    )

    assert_safe_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    population = build_population(generator_seed=generator_seed, variants_per_group=variants_per_group)
    assert_population_oracle_contract(population, variants_per_group=variants_per_group)
    split_assignment = assign_splits(FRESH_SCENARIO_GROUPS, split_seed=split_seed)
    leakage = audit_split_leakage(population, split_assignment)
    structural_units = 0 if not leakage["adequate_independent_groups_for_confirmatory"] else leakage[
        "unique_structural_ancestor_count"
    ]

    sizing: ConfirmatorySizingResult | None = None
    paired_summaries = None
    if effect_spec is not None:
        if observed_baselines_path is None or policy_contrasts_path is None:
            raise ValueError("confirmatory sizing requires --observed-baselines and --policy-contrasts")
        baselines = load_observed_baselines(observed_baselines_path)
        contrasts = load_policy_contrasts(policy_contrasts_path)
        spec = ConfirmatoryEffectSpec(
            min_unsafe_release_reduction=float(effect_spec["min_unsafe_release_reduction"]),
            max_legitimate_completion_loss=float(effect_spec["max_legitimate_completion_loss"]),
            alpha=float(effect_spec["alpha"]),
            power=float(effect_spec["power"]),
        )
        paired_summaries = development_paired_summaries(
            population,
            split_assignment=split_assignment,
            observed_baselines=baselines,
            policy_contrasts=contrasts,
        )
        sizing = simulate_confirmatory_sizing(
            spec,
            paired_summaries,
            rng_seed=sizing_rng_seed,
            simulations=simulations,
            max_independent_groups=max_independent_groups,
            independent_structural_units_available=structural_units,
        )

    policy_assumptions = {
        "acquisition_policies_planned": list(budgeted_recovery.AcquisitionPolicyName),
        "learned_policy_fit_partition": "development",
        "calibration_partition": "calibration",
        "sealed_evaluation_partition": "sealed_test",
        "action_gate": "ActionGate without oracle label access",
    }
    model_assumptions = {
        "hold_out_model_families_for_policy_fit": True,
        "candidates": ["gemma-4-31b-it", "qwen3.5-397b"],
        "not_pooled_across_architectures_in_primary_analysis": True,
    }
    prompt_assumptions = {
        "supervisor_outputs_json": {
            "action": ["release", "hold", "acquire"],
            "reason": "short string",
        },
        "forbidden_prompt_fields": sorted(
            {"case_id", "control", "legitimate", "oracle_details", "scenario_group"}
        ),
    }
    attack_assumptions = {
        "development_visibility": "development trajectories and frozen policy",
        "sealed_attacks": [
            "random_outages",
            "selective_omission",
            "strategic_timing",
            "untrusted_content",
            "review_budget_exhaustion",
        ],
        "cannot_alter_authenticated_records_primary_regime": True,
    }
    resource_assumptions = {
        "collection_spend_cap_separate_from_policy_budget": True,
        "no_arbitrary_cost_on_free_local_ops": True,
        "measured_token_retrieval_review_latency_separate": True,
        "max_independent_groups_search": max_independent_groups,
        "bootstrap_simulations": simulations,
        "sizing_mode": "coarse_grid_vectorized_exploratory",
    }

    manifest = build_prepare_manifest(
        generator_seed=generator_seed,
        split_seed=split_seed,
        source_hashes=source_hashes(),
        split_assignment=split_assignment,
        leakage_report=leakage,
        sizing=sizing,
        policy_assumptions=policy_assumptions,
        resource_assumptions=resource_assumptions,
        attack_assumptions=attack_assumptions,
        prompt_assumptions=prompt_assumptions,
        model_assumptions=model_assumptions,
        population_count=len(population),
        variants_per_group=variants_per_group,
    )

    population_path = output_dir / "population.jsonl"
    with population_path.open("w", encoding="utf-8") as handle:
        for row in population:
            handle.write(canonical_json(row.to_document()) + "\n")

    save_json_atomic(output_dir / "split_assignment.json", dict(split_assignment))
    save_json_atomic(output_dir / "split_leakage_audit.json", leakage)
    save_json_atomic(output_dir / "live_worker_record_schema.json", live_worker_record_schema())
    save_json_atomic(output_dir / "source_hashes.json", source_hashes())
    if paired_summaries is not None:
        save_json_atomic(
            output_dir / "development_paired_summaries.json",
            [asdict(row) for row in paired_summaries],
        )
    if sizing is not None:
        save_json_atomic(output_dir / "confirmatory_sizing.json", asdict(sizing))

    digest_input = canonical_json({k: v for k, v in manifest.items() if k != "manifest_sha256"}).encode(
        "utf-8"
    )
    manifest["manifest_sha256"] = _content_sha256(digest_input)
    save_json_atomic(output_dir / "manifest.json", manifest)

    return {
        "output_dir": str(output_dir),
        "population_rows": len(population),
        "leakage_ok": leakage["ok"],
        "adequate_independent_groups": leakage["adequate_independent_groups_for_confirmatory"],
        "sizing_underpowered": None if sizing is None else sizing.underpowered,
        "manifest_sha256": manifest["manifest_sha256"],
        "execution_status": manifest["execution"]["status"],
        "registration_status": manifest["registration"]["status"],
        "source_hashes": manifest["source_hashes"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare budgeted recovery study artifacts (no API calls).")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--generator-seed", type=int, default=20261112)
    parser.add_argument("--split-seed", type=int, default=20261113)
    parser.add_argument("--variants-per-group", type=int, default=1)
    parser.add_argument("--simulations", type=int, default=512)
    parser.add_argument("--max-independent-groups", type=int, default=128)
    parser.add_argument("--sizing-rng-seed", type=int, default=20261114)
    parser.add_argument("--min-unsafe-release-reduction", type=float)
    parser.add_argument("--max-legitimate-completion-loss", type=float)
    parser.add_argument("--alpha", type=float)
    parser.add_argument("--power", type=float)
    parser.add_argument("--observed-baselines", type=Path)
    parser.add_argument("--policy-contrasts", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    effect_keys = (
        args.min_unsafe_release_reduction,
        args.max_legitimate_completion_loss,
        args.alpha,
        args.power,
    )
    if any(value is not None for value in effect_keys):
        if any(value is None for value in effect_keys):
            parser.error(
                "sizing requires all of --min-unsafe-release-reduction, "
                "--max-legitimate-completion-loss, --alpha, --power"
            )
        effect_spec = {
            "min_unsafe_release_reduction": args.min_unsafe_release_reduction,
            "max_legitimate_completion_loss": args.max_legitimate_completion_loss,
            "alpha": args.alpha,
            "power": args.power,
        }
    else:
        effect_spec = None

    result = prepare_study(
        args.output_dir,
        generator_seed=args.generator_seed,
        split_seed=args.split_seed,
        variants_per_group=args.variants_per_group,
        effect_spec=effect_spec,
        observed_baselines_path=args.observed_baselines,
        policy_contrasts_path=args.policy_contrasts,
        simulations=args.simulations,
        max_independent_groups=args.max_independent_groups,
        sizing_rng_seed=args.sizing_rng_seed,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
