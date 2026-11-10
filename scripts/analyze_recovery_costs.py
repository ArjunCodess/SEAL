"""Offline cost-only report for the recovery development pilot (no API calls)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]

SOURCE_FILE_NAMES = {
    "recovery_fixtures_py": "recovery_fixtures.py",
    "run_recovery_pilot_py": "run_recovery_pilot.py",
}

TERMINAL_CALL_STATUSES = frozenset(
    {"completed", "failed", "error", "invalid", "refused_budget"}
)
REQUEST_TIMEOUT_SECONDS = 60

SCIENTIFIC_CAVEATS = [
    "Development-only catalog; not confirmatory and no safety or superiority claim.",
    "Record bodies are a few hundred bytes each; full retrieval is not an operational constraint on this catalog.",
    "The manifest spend cap is a collection reservation ceiling, not an oversight-policy budget.",
    "Do not report unsafe-release rates, power, or pooled model headlines from this pilot.",
    "Selective-acquisition superiority requires an external go rule and natural workflow archives.",
]


class CostAnalysisError(Exception):
    pass


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _import_pilot() -> Any:
    sys.path.insert(0, str(ROOT))
    from scripts import run_recovery_pilot as pilot

    return pilot


def validate_output_dir(output_dir: Path) -> Path:
    root = output_dir.resolve()
    if root == ROOT or root in ROOT.parents:
        raise ValueError("Report output cannot replace the repository or its ancestors")
    for name in ("data", "src", "scripts", "tests", "paper", "docs", "experiments", ".git"):
        protected = (ROOT / name).resolve()
        if root == protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError("Report output must be separate from source and collected inputs")
    return root


def pilot_paths(pilot_dir: Path) -> dict[str, Path]:
    root = pilot_dir.resolve()
    return {
        "root": root,
        "manifest": root / "manifest.json",
        "cases": root / "cases.json",
        "ledger": root / "ledger.json",
        "calls": root / "calls",
        "source": root / "source",
        "protocol": root / "protocol.json",
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


def percentile(sorted_values: Sequence[float], p: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (p / 100.0)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(sorted_values[low])
    weight = rank - low
    return float(sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight)


def load_cases_document(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    cases = doc.get("cases")
    if not isinstance(cases, list):
        raise CostAnalysisError("cases.json must contain a cases array")
    return cases


def case_content_fingerprint(case: Mapping[str, Any]) -> str:
    records = case.get("records") or []
    record_part = tuple(
        (str(row.get("record_id")), str(row.get("kind")), str(row.get("body")))
        for row in records
    )
    payload = canonical_json(
        {
            "task": case.get("task"),
            "proposal": case.get("proposal"),
            "worker_claims": case.get("worker_claims"),
            "records": record_part,
        }
    )
    return content_sha256(payload.encode("utf-8"))


def record_bundle_fingerprint(case: Mapping[str, Any]) -> str:
    records = case.get("records") or []
    record_part = tuple(
        (str(row.get("record_id")), str(row.get("kind")), str(row.get("body")))
        for row in records
    )
    payload = canonical_json({"task": case.get("task"), "records": record_part})
    return content_sha256(payload.encode("utf-8"))


def verify_source_snapshot(manifest: Mapping[str, Any], source_dir: Path) -> list[str]:
    errors: list[str] = []
    expected = manifest.get("source_hashes") or {}
    for key, digest in expected.items():
        fname = SOURCE_FILE_NAMES.get(key)
        if not fname:
            errors.append(f"unknown source_hashes key: {key}")
            continue
        path = source_dir / fname
        if not path.is_file():
            errors.append(f"missing source snapshot file: {path}")
            continue
        actual = content_sha256(path.read_bytes())
        if actual != digest:
            errors.append(f"source snapshot hash mismatch for {fname}: expected {digest}, got {actual}")
    return errors


def verify_request_pin(call: Mapping[str, Any], pilot: Any) -> str | None:
    request = call.get("request")
    if not isinstance(request, dict):
        return "call missing request payload for hash verification"
    expected = call.get("request_sha256")
    actual = content_sha256(pilot.canonical_json(request).encode("utf-8"))
    if expected != actual:
        return f"request_sha256 mismatch: file claims {expected}, payload hashes to {actual}"
    return None


def expected_episode_ids(
    manifest: Mapping[str, Any], cases: Sequence[Mapping[str, Any]], pilot: Any
) -> dict[str, dict[str, str]]:
    protocol = manifest.get("protocol_sha256")
    if not isinstance(protocol, str):
        raise CostAnalysisError("manifest missing protocol_sha256")
    models = manifest.get("models") or []
    policies = manifest.get("policies") or []
    mapping: dict[str, dict[str, str]] = {}
    for case in cases:
        case_id = str(case["case_id"])
        for model in models:
            for policy in policies:
                eid = pilot.episode_id(protocol, str(model), str(policy), case_id)
                mapping[eid] = {
                    "case_id": case_id,
                    "model": str(model),
                    "policy": str(policy),
                    "scenario_group": str(case.get("scenario_group")),
                    "control": str(case.get("control")),
                    "family": str(case.get("family")),
                }
    return mapping


def status_bucket(status: str | None) -> str:
    if status == "completed":
        return "completed"
    if status in {"failed", "error"}:
        return "failed"
    if status == "invalid":
        return "invalid"
    if status == "refused_budget":
        return "refused_budget"
    if status == "pending":
        return "pending"
    if status is None:
        return "missing"
    return "other"


def usage_derived_credits(
    model: str, usage: Mapping[str, Any] | None, price_table: Mapping[str, Any]
) -> float | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        return None
    if input_tokens < 0 or output_tokens < 0:
        return None
    rates = price_table.get(model)
    if not isinstance(rates, dict):
        return None
    in_rate = float(rates["input_price_per_million"])
    out_rate = float(rates["output_price_per_million"])
    total = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    return total if math.isfinite(total) else None


def duplicate_observation_report(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_group[str(case.get("scenario_group"))].append(case)

    permitted_refusal: dict[str, int] = {}
    mistake_violation: dict[str, int] = {}

    for group, rows in sorted(by_group.items()):
        fps: dict[str, list[str]] = defaultdict(list)
        bundle_fps: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            control = str(row.get("control"))
            fps[case_content_fingerprint(row)].append(control)
            bundle_fps[record_bundle_fingerprint(row)].append(control)
        pr_count = sum(
            1
            for controls in fps.values()
            if "permitted_collaboration" in controls and "collusion_refusal" in controls
        )
        if pr_count:
            permitted_refusal[group] = pr_count
        mv_count = sum(
            1
            for controls in bundle_fps.values()
            if "ordinary_mistake" in controls and "instructed_violation" in controls
        )
        if mv_count:
            mistake_violation[group] = mv_count

    return {
        "permitted_refusal_duplicate_groups": permitted_refusal,
        "mistake_violation_duplicate_groups": mistake_violation,
        "permitted_refusal_duplicate_group_count": len(permitted_refusal),
        "mistake_violation_duplicate_group_count": len(mistake_violation),
    }


def compute_record_size_rows(
    cases: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    pilot: Any,
    *,
    seed: int,
) -> list[dict[str, Any]]:
    from types import SimpleNamespace

    models = manifest.get("models") or []
    policies = manifest.get("policies") or []
    rows: list[dict[str, Any]] = []

    def as_case(raw: Mapping[str, Any]) -> Any:
        records = tuple(
            SimpleNamespace(
                record_id=str(r["record_id"]),
                kind=str(r["kind"]),
                body=str(r["body"]),
            )
            for r in raw.get("records") or []
        )
        return SimpleNamespace(
            case_id=str(raw["case_id"]),
            family=str(raw.get("family")),
            scenario_group=str(raw.get("scenario_group")),
            control=str(raw.get("control")),
            task=str(raw.get("task")),
            proposal=str(raw.get("proposal")),
            worker_claims=tuple(str(x) for x in raw.get("worker_claims") or []),
            records=records,
            legitimate=bool(raw.get("legitimate")),
        )

    for raw in cases:
        case = as_case(raw)
        all_bytes = sum(len(r.body.encode("utf-8")) for r in case.records)
        for policy in policies:
            selected = pilot.select_policy_records(str(policy), case, seed=seed)
            selected_bytes = sum(len(r.body.encode("utf-8")) for r in selected)
            row_base = {
                "scenario_group": case.scenario_group,
                "family": case.family,
                "control": case.control,
                "case_id": case.case_id,
                "policy": policy,
                "all_record_count": len(case.records),
                "all_record_bytes": all_bytes,
                "selected_record_count": len(selected),
                "selected_record_bytes": selected_bytes,
            }
            for model in models:
                meta = [pilot.record_metadata(r) for r in case.records]
                observation = pilot.build_llm_observation(
                    case, str(policy), selected, archive_metadata=meta
                )
                payload = pilot.build_request_payload(str(model), observation)
                request_bytes = pilot.request_utf8_byte_upper_bound(payload)
                claims_obs = pilot.build_llm_observation(case, "claims", (), archive_metadata=meta)
                claims_payload = pilot.build_request_payload(str(model), claims_obs)
                claims_bytes = pilot.request_utf8_byte_upper_bound(claims_payload)
                rows.append(
                    {
                        **row_base,
                        "model": str(model),
                        "request_utf8_bytes": request_bytes,
                        "claims_request_utf8_bytes": claims_bytes,
                        "full_minus_claims_utf8_bytes": request_bytes - claims_bytes,
                    }
                )
    return rows


@dataclass
class EpisodeView:
    episode_id: str
    scheduled: bool
    status: str
    model: str
    policy: str
    scenario_group: str
    control: str
    family: str
    case_id: str
    input_tokens: int
    output_tokens: int
    usage_credits: float | None
    charged_credits: float | None
    ledger_credits: float | None
    latency_ms: float | None
    selected_record_count: int | None
    input_utf8_bytes: int | None
    request_hash_ok: bool
    call_present: bool


def aggregate_key_model_policy(view: EpisodeView) -> tuple[str, str]:
    return (view.model, view.policy)


def analyze_recovery_costs(
    pilot_dir: Path,
    output_dir: Path,
    *,
    allow_partial: bool = False,
    write_plot: bool = False,
) -> dict[str, Any]:
    pilot = _import_pilot()
    out_root = validate_output_dir(output_dir)
    paths = pilot_paths(pilot_dir)
    if out_root == paths["root"] or out_root.is_relative_to(paths["root"]) or paths["root"].is_relative_to(out_root):
        raise CostAnalysisError("Report output must not overlap pilot inputs")

    for required in ("manifest", "cases", "ledger"):
        if not paths[required].is_file():
            raise CostAnalysisError(f"missing required pilot file: {paths[required]}")
    if not paths["calls"].is_dir():
        raise CostAnalysisError(f"missing calls directory: {paths['calls']}")

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    cases = load_cases_document(paths["cases"])
    ledger = json.loads(paths["ledger"].read_text(encoding="utf-8"))
    seed = int(manifest.get("seed", pilot.DEFAULT_SEED))
    schedule_count = int(manifest.get("schedule_count", 0))
    case_ids_manifest = list(manifest.get("case_ids") or [])
    case_ids_file = [str(c["case_id"]) for c in cases]
    if case_ids_manifest != case_ids_file:
        raise CostAnalysisError("cases.json case_ids do not match manifest.case_ids")

    source_errors = verify_source_snapshot(manifest, paths["source"])
    if source_errors:
        raise CostAnalysisError("; ".join(source_errors))

    expected = expected_episode_ids(manifest, cases, pilot)
    if len(expected) != schedule_count:
        raise CostAnalysisError(
            f"rebuilt schedule {len(expected)} != manifest schedule_count {schedule_count}"
        )

    call_by_id: dict[str, dict[str, Any]] = {}
    for path in paths["calls"].glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        eid = str(row.get("episode_id") or path.stem)
        call_by_id[eid] = row

    ledger_eps = ledger.get("episodes") or {}
    price_table = (manifest.get("budget") or {}).get("per_model_prices") or {}

    hash_errors: list[str] = []
    episode_views: list[EpisodeView] = []

    for eid, meta in sorted(expected.items()):
        call = call_by_id.get(eid)
        ledger_row = ledger_eps.get(eid)
        if call is None:
            status = "missing"
            if ledger_row and ledger_row.get("status") == "pending":
                status = "pending"
            episode_views.append(
                EpisodeView(
                    episode_id=eid,
                    scheduled=True,
                    status=status,
                    model=meta["model"],
                    policy=meta["policy"],
                    scenario_group=meta["scenario_group"],
                    control=meta["control"],
                    family=meta["family"],
                    case_id=meta["case_id"],
                    input_tokens=0,
                    output_tokens=0,
                    usage_credits=None,
                    charged_credits=None,
                    ledger_credits=float(ledger_row["charged_cost"])
                    if isinstance(ledger_row, Mapping) and isinstance(ledger_row.get("charged_cost"), (int, float))
                    else None,
                    latency_ms=None,
                    selected_record_count=None,
                    input_utf8_bytes=None,
                    request_hash_ok=False,
                    call_present=False,
                )
            )
            continue

        pin_error = verify_request_pin(call, pilot)
        request_hash_ok = pin_error is None
        if pin_error:
            hash_errors.append(f"{eid}: {pin_error}")

        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        usage_credits = usage_derived_credits(meta["model"], usage, price_table)
        charged = call.get("charged_cost")
        charged_credits = float(charged) if isinstance(charged, (int, float)) and math.isfinite(float(charged)) else None
        ledger_credits = None
        if isinstance(ledger_row, Mapping):
            lc = ledger_row.get("charged_cost")
            if isinstance(lc, (int, float)) and math.isfinite(float(lc)):
                ledger_credits = float(lc)

        latency = call.get("latency_ms")
        latency_ms = float(latency) if isinstance(latency, (int, float)) and math.isfinite(float(latency)) else None

        episode_views.append(
            EpisodeView(
                episode_id=eid,
                scheduled=True,
                status=status_bucket(call.get("status")),
                model=meta["model"],
                policy=meta["policy"],
                scenario_group=meta["scenario_group"],
                control=meta["control"],
                family=meta["family"],
                case_id=meta["case_id"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_credits=usage_credits,
                charged_credits=charged_credits,
                ledger_credits=ledger_credits,
                latency_ms=latency_ms,
                selected_record_count=int(call["selected_record_count"])
                if isinstance(call.get("selected_record_count"), int)
                else None,
                input_utf8_bytes=int(call["input_utf8_bytes"])
                if isinstance(call.get("input_utf8_bytes"), int)
                else None,
                request_hash_ok=request_hash_ok,
                call_present=True,
            )
        )

    extra_call_ids = sorted(set(call_by_id) - set(expected))
    pending_ledger = [eid for eid, row in ledger_eps.items() if row.get("status") == "pending"]
    missing_scheduled = [v.episode_id for v in episode_views if v.status in {"missing", "pending"}]

    partial = bool(missing_scheduled or pending_ledger or extra_call_ids)
    if hash_errors:
        raise CostAnalysisError("request hash verification failed: " + "; ".join(hash_errors[:5]))

    if partial and not allow_partial:
        raise CostAnalysisError(
            f"incomplete pilot: missing_or_pending={len(missing_scheduled)} "
            f"pending_ledger={len(pending_ledger)} extra_calls={len(extra_call_ids)}; "
            "pass --allow-partial to emit a marked partial report"
        )

    def count_status(views: Sequence[EpisodeView]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for view in views:
            counts[view.status] += 1
        counts["scheduled"] = len(views)
        return dict(counts)

    def aggregate_slice(
        views: Sequence[EpisodeView],
        key_fn: Any,
    ) -> dict[str, Any]:
        buckets: dict[Any, list[EpisodeView]] = defaultdict(list)
        for view in views:
            buckets[key_fn(view)].append(view)

        result: dict[str, Any] = {}
        for key, items in sorted(buckets.items(), key=lambda pair: str(pair[0])):
            statuses = count_status(items)
            latencies = sorted(
                float(v.latency_ms)
                for v in items
                if v.status == "completed" and v.latency_ms is not None
            )
            usage_total = sum(v.usage_credits or 0.0 for v in items if v.usage_credits is not None)
            charged_total = sum(v.charged_credits or 0.0 for v in items)
            ledger_total = sum(v.ledger_credits or 0.0 for v in items if v.ledger_credits is not None)
            in_tok = sum(v.input_tokens for v in items)
            out_tok = sum(v.output_tokens for v in items)
            label = key if isinstance(key, str) else "|".join(key)
            result[label] = {
                "status_counts": statuses,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "usage_derived_credits_sum": usage_total,
                "charged_credits_sum_conservative": charged_total,
                "ledger_credits_sum": ledger_total,
                "latency_ms_p50": percentile(latencies, 50),
                "latency_ms_p95": percentile(latencies, 95),
                "latency_completed_n": len(latencies),
                "latency_timed_out_caveat": (
                    f"Client timeout is {REQUEST_TIMEOUT_SECONDS}s; "
                    "latency near that bound may be truncated."
                ),
            }
        return result

    model_policy = aggregate_slice(episode_views, aggregate_key_model_policy)
    by_scenario = aggregate_slice(episode_views, lambda v: v.scenario_group)

    charged_call_sum = sum(v.charged_credits or 0.0 for v in episode_views if v.charged_credits is not None)
    charged_ledger_sum = sum(
        float(row.get("charged_cost") or 0.0)
        for row in ledger_eps.values()
        if isinstance(row, Mapping) and isinstance(row.get("charged_cost"), (int, float))
    )
    consistency = {
        "scheduled_episodes": len(expected),
        "call_files_on_disk": len(call_by_id),
        "extra_call_ids": extra_call_ids,
        "missing_or_pending_scheduled": missing_scheduled,
        "pending_ledger_episode_ids": pending_ledger,
        "ledger_spent_total": ledger.get("spent_total"),
        "ledger_request_count": ledger.get("request_count"),
        "sum_charged_from_calls_scheduled": charged_call_sum,
        "sum_charged_from_ledger_episodes": charged_ledger_sum,
        "ledger_vs_call_charge_delta": charged_ledger_sum - charged_call_sum,
        "all_scheduled_calls_accounted": len(missing_scheduled) == 0 and len(extra_call_ids) == 0,
        "request_pins_verified": len(hash_errors) == 0,
    }

    duplicate_report = duplicate_observation_report(cases)
    record_rows = compute_record_size_rows(cases, manifest, pilot, seed=seed)

    partial_banner = None
    if partial:
        partial_banner = (
            "**PARTIAL COLLECTION** — report includes pending/missing episodes or extra call files. "
            "Do not treat totals as final."
        )

    summary = {
        "pilot_label": manifest.get("pilot_label"),
        "protocol_sha256": manifest.get("protocol_sha256"),
        "development_only": bool(manifest.get("development_only", True)),
        "partial_collection": partial,
        "allow_partial_used": allow_partial,
        "source_hashes": manifest.get("source_hashes"),
        "schedule_count": schedule_count,
        "episodes_analyzed": len(episode_views),
        "status_counts_all_scheduled": count_status(episode_views),
        "by_model_policy": model_policy,
        "by_scenario_group": by_scenario,
        "duplicate_observations": duplicate_report,
        "consistency_checks": consistency,
        "scientific_caveats": SCIENTIFIC_CAVEATS,
        "cost_only_scope": True,
        "forbidden_metrics": [
            "unsafe_release_rate",
            "power",
            "superiority",
            "production_signature",
        ],
        "collection_cap_note": "manifest.budget.spend_cap is a collection reservation ceiling, not policy oversight budget.",
        "full_retrieval_constraint_note": (
            "On this nine-template catalog, full record bodies are a few hundred bytes; "
            "full retrieval is not evidence of an external operational constraint."
        ),
    }

    out_root.mkdir(parents=True, exist_ok=True)
    save_json_atomic(out_root / "summary.json", summary)

    mp_path = out_root / "model_policy_costs.csv"
    mp_fields = [
        "model",
        "policy",
        "scheduled",
        "completed",
        "failed",
        "invalid",
        "refused_budget",
        "missing",
        "pending",
        "input_tokens",
        "output_tokens",
        "usage_derived_credits_sum",
        "charged_credits_sum_conservative",
        "ledger_credits_sum",
        "latency_ms_p50",
        "latency_ms_p95",
        "latency_completed_n",
    ]
    with mp_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=mp_fields)
        writer.writeheader()
        for label, row in sorted(model_policy.items()):
            model, policy = label.split("|", 1)
            statuses = row["status_counts"]
            writer.writerow(
                {
                    "model": model,
                    "policy": policy,
                    "scheduled": statuses.get("scheduled", 0),
                    "completed": statuses.get("completed", 0),
                    "failed": statuses.get("failed", 0),
                    "invalid": statuses.get("invalid", 0),
                    "refused_budget": statuses.get("refused_budget", 0),
                    "missing": statuses.get("missing", 0),
                    "pending": statuses.get("pending", 0),
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "usage_derived_credits_sum": row["usage_derived_credits_sum"],
                    "charged_credits_sum_conservative": row["charged_credits_sum_conservative"],
                    "ledger_credits_sum": row["ledger_credits_sum"],
                    "latency_ms_p50": row["latency_ms_p50"],
                    "latency_ms_p95": row["latency_ms_p95"],
                    "latency_completed_n": row["latency_completed_n"],
                }
            )

    rs_path = out_root / "record_sizes.csv"
    rs_fields = list(record_rows[0].keys()) if record_rows else ["scenario_group"]
    with rs_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rs_fields)
        writer.writeheader()
        writer.writerows(record_rows)

    md_lines = [
        "# Recovery pilot cost report (offline)",
        "",
        f"**Pilot label:** `{manifest.get('pilot_label')}`",
        "",
    ]
    if partial_banner:
        md_lines.extend([partial_banner, ""])
    md_lines.extend(
        [
            "Cost-only aggregation. No unsafe-release rates, power estimates, or superiority claims.",
            "",
            "## Scientific caveats",
            "",
        ]
    )
    md_lines.extend(f"- {line}" for line in SCIENTIFIC_CAVEATS)
    md_lines.extend(["", "## Collection status", ""])
    md_lines.append(f"- Scheduled episodes: {schedule_count}")
    md_lines.append(f"- Status histogram (scheduled set): `{summary['status_counts_all_scheduled']}`")
    if partial:
        md_lines.append(f"- Missing or pending ids (sample): `{missing_scheduled[:8]}`")
    md_lines.extend(["", "## Duplicate observation flags", ""])
    md_lines.append(
        f"- Permitted/refusal duplicate scenario groups: {duplicate_report['permitted_refusal_duplicate_group_count']}"
    )
    md_lines.append(
        f"- Mistake/violation duplicate scenario groups: {duplicate_report['mistake_violation_duplicate_group_count']}"
    )
    md_lines.extend(["", "## Consistency", ""])
    for key, value in consistency.items():
        md_lines.append(f"- `{key}`: {value!r}")
    md_lines.extend(["", "## Model / policy costs", ""])
    for label, row in sorted(model_policy.items()):
        md_lines.append(
            f"### {label.replace('|', ' · ')} — statuses `{row['status_counts']}`; "
            f"usage credits {row['usage_derived_credits_sum']:.8f}; "
            f"charged conservative {row['charged_credits_sum_conservative']:.8f}; "
            f"latency p50/p95 {row['latency_ms_p50']}/{row['latency_ms_p95']} "
            f"(n={row['latency_completed_n']})"
        )
    (out_root / "results.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    if write_plot:
        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise CostAnalysisError("matplotlib required for --plot") from exc
        labels: list[str] = []
        charged_vals: list[float] = []
        usage_vals: list[float] = []
        for label, row in sorted(model_policy.items()):
            labels.append(label.replace("|", "\n"))
            charged_vals.append(float(row["charged_credits_sum_conservative"]))
            usage_vals.append(float(row["usage_derived_credits_sum"]))
        width = 0.35
        x = list(range(len(labels)))
        fig, ax = plt.subplots(figsize=(max(8, len(labels) * 0.45), 5))
        ax.bar([i - width / 2 for i in x], usage_vals, width, label="usage-derived credits")
        ax.bar([i + width / 2 for i in x], charged_vals, width, label="charged conservative credits")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel("Credits (manifest price table)")
        ax.set_title("Recovery pilot cost by model and policy")
        ax.legend()
        fig.tight_layout()
        fig.savefig(out_root / "model_policy_costs.png", dpi=120)
        plt.close(fig)

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline recovery pilot cost-only report")
    parser.add_argument(
        "--pilot-dir",
        type=Path,
        required=True,
        help="Prepared pilot tree (manifest, cases.json, ledger, calls/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "build" / "recovery-costs",
        help="Writable report directory (default: build/recovery-costs)",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Emit a visibly marked partial report when collection is incomplete",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Write optional model_policy_costs.png (requires matplotlib)",
    )
    args = parser.parse_args(argv)
    try:
        summary = analyze_recovery_costs(
            args.pilot_dir,
            args.output_dir,
            allow_partial=bool(args.allow_partial),
            write_plot=bool(args.plot),
        )
    except (CostAnalysisError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "partial_collection": summary.get("partial_collection"),
                "episodes_analyzed": summary.get("episodes_analyzed"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
