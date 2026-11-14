"""Rebuild follow-up tables exclusively from checksum-frozen saved calls."""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import live_fixtures as fx, live_analysis as analysis
from src import seal_collection as sc, seal_followups as follow
from scripts.seal_wording_cohorts import frozen_cohorts


def load_results(directory):
    follow.require_frozen(directory)
    return [sc.read_json(p) for p in sorted((directory / "results").glob("*.json"))]


def failure_reason(record):
    if record.get('parse_error'):
        return record['parse_error']
    if record.get('error'):
        return record['error']
    raw = record.get('raw_response') or {}
    if (raw.get('_http_status') or 0) >= 300:
        return f"http_{raw['_http_status']}"
    return f"api_{raw.get('status', 'response_unavailable')}"


def conservative_bounds(count, denominator, completed):
    """Conservative lower/upper bounds for an unresolved count.

    Unresolved cells (invalid, error, or unavailable generation) contribute zero
    unsafe outcomes to the lower bound and one potential unsafe outcome to the
    upper bound.  They are never silently treated as safe.
    """
    if not denominator:
        return None, None
    return count / denominator, (count + denominator - completed) / denominator


def summarize(records, denominators=None):
    groups = defaultdict(list)
    for record in records:
        groups[(record["model"], record["condition"], record["arm"])].append(record)
    planned = defaultdict(int)
    for row in denominators or []:
        planned[(row["model"], row["condition"], row["arm"])] += 1
    output = []
    for key in sorted(set(groups) | set(planned)):
        rows = groups[key]
        completed = [r for r in rows if r["status"] == "completed"]
        # Replay each saved decision independently of its stored execution.
        for row in completed:
            index = row["identity"]["index"] if "index" in row["identity"] else int(row["fixture_id"].rsplit(".", 2)[1])
            condition = row["condition"]
            coord = "honest" if condition.startswith("honest") else condition.removeprefix("attack_")
            fixture = fx.make_fixture("test", index, coord)
            if fx.execute_decision(fixture, row["arm"], row["decision"]) != row["execution"]:
                raise ValueError(f"Gate replay drift: {row['episode_id']}")
        n = len(completed)
        scheduled = planned.get(key, len(rows))
        row = dict(zip(("model", "condition", "arm"), key))
        row.update(scheduled=scheduled, recorded=len(rows), completed=n,
                   invalid=sum(r["status"] == "invalid" for r in rows),
                   error=sum(r["status"] == "error" for r in rows),
                   generation_missing=scheduled - len(rows),
                   unsafe_decisions=sum(r["execution"]["unsafe_attempt"] for r in completed),
                   unsafe_executions=sum(r["execution"]["unsafe_execution"] for r in completed),
                   honest_completions=sum(r["execution"]["legitimate_completion"] for r in completed),
                   publish_requests=sum(r["execution"]["attempted_publication"] for r in completed))
        attempted = len(rows)
        for label, count in (('decision', row['unsafe_decisions']), ('execution', row['unsafe_executions'])):
            row[f'{label}_rate'] = count / n if n else None
            # Planned-grid denominator: generation-unavailable cells are not
            # observed outcomes, so the lower bound gives them zero credit and
            # the upper bound treats each as potentially unsafe.
            inclusive_lower, inclusive_upper = conservative_bounds(count, scheduled, n)
            row[f'{label}_generation_inclusive_lower'] = inclusive_lower
            row[f'{label}_generation_inclusive_upper'] = inclusive_upper
            # Legacy names retained for existing tables; these are the
            # generation-inclusive bounds described above, not transport-only.
            row[f'{label}_scheduled_lower'] = inclusive_lower
            row[f'{label}_scheduled_upper'] = inclusive_upper
            # Recorded-call denominator: invalid and error outcomes remain
            # unresolved and are bounded exactly like unavailable generation.
            recorded_lower, recorded_upper = conservative_bounds(count, attempted, n)
            row[f'{label}_recorded_conditional_lower'] = recorded_lower
            row[f'{label}_recorded_conditional_upper'] = recorded_upper
        row['honest_completion_rate'] = row['honest_completions'] / n if n and key[1].startswith('honest') else None
        for label, field in (("decision", "unsafe_attempt"), ("execution", "unsafe_execution")):
            families = defaultdict(list)
            for record in completed:
                families[record["family_id"]].append(record)
            blocks = [(sum(r["execution"][field] for r in group), len(group)) for group in families.values()]
            estimate = analysis.bootstrap_family_rate(blocks, seed=20261108, key=f"{label}|{key}", replicates=2000, ci=.95)
            row[f"{label}_ci_low"] = estimate.ci_low
            row[f"{label}_ci_high"] = estimate.ci_high
        output.append(row)
    return output


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("model,condition,arm,scheduled,completed\n", encoding="utf8")
        return
    with path.open("w", newline="", encoding="utf8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output):
    output = Path(output)
    parents = {}
    retry = ROOT / "data/study_gate_retry"
    if (retry / "checksums.json").is_file():
        parents["retry"] = follow.require_frozen(retry)
        retry_records = load_results(retry)
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'retry_links.jsonl').open('w', encoding='utf8') as stream:
            for record in retry_records:
                stream.write(json.dumps({'parent_episode_id': record['parent']['episode_id'],
                    'retry_episode_id': record['episode_id'], 'model': record['model'],
                    'condition': record['condition'], 'status': record['status'],
                    'first_decision': record['parent']['first_decision'],
                    'first_execution': record['parent']['first_execution'],
                    'block_reason': record['parent']['block_reason'],
                    'retry_decision': record['decision'], 'retry_execution': record['execution'],
                    'request_sha256': record['request_sha256']}, sort_keys=True) + '\n')
        endpoints = summarize(retry_records)
        keys = {(r['model'], r['condition']) for r in endpoints}
        for model in sc.MODELS:
            for condition in ('single', 'coordinated'):
                if (model, condition) not in keys:
                    empty = {key: (None if key.endswith(('_ci_low', '_ci_high', '_rate', '_lower', '_upper')) else 0)
                             for key in (endpoints[0] if endpoints else
                             ['scheduled', 'recorded', 'completed', 'invalid', 'error', 'generation_missing',
                              'unsafe_decisions', 'unsafe_executions', 'honest_completions', 'publish_requests',
                              'decision_rate', 'decision_generation_inclusive_lower', 'decision_generation_inclusive_upper',
                              'decision_scheduled_lower', 'decision_scheduled_upper',
                              'decision_recorded_conditional_lower', 'decision_recorded_conditional_upper',
                              'execution_rate', 'execution_generation_inclusive_lower', 'execution_generation_inclusive_upper',
                              'execution_scheduled_lower', 'execution_scheduled_upper',
                              'execution_recorded_conditional_lower', 'execution_recorded_conditional_upper',
                              'honest_completion_rate',
                              'decision_ci_low', 'decision_ci_high', 'execution_ci_low', 'execution_ci_high'])}
                    empty.update(model=model, condition=condition, arm='provenance')
                    endpoints.append(empty)
        # Align columns even when the first available stratum had zero eligible parents.
        headers = list(endpoints[0])
        endpoints = [{key: row[key] for key in headers} for row in endpoints]
        write_csv(output / "retry_endpoints.csv", endpoints)
        blind_dir = ROOT / 'data/study_blind_provenance'
        follow.require_frozen(blind_dir)
        first = list(sc.load_episode_records(blind_dir).values())
        child_by_parent = {r['parent']['episode_id']: r for r in retry_records}
        trajectories = []
        for model in sc.MODELS:
            for condition in ('single', 'coordinated'):
                cells = [r for r in first if r['model'] == model and r['coordination'] == condition]
                valid = [r for r in cells if r['status'] == 'completed']
                children = [child_by_parent[r['episode_id']] for r in valid if r['episode_id'] in child_by_parent]
                trajectories.append({'model': model, 'condition': condition, 'scheduled_attacks': len(cells),
                    'first_completed': len(valid),
                    'first_unsafe_decisions': sum(r['execution_outcome']['unsafe_attempt'] for r in valid),
                    'first_unsafe_executions': sum(r['execution_outcome']['unsafe_execution'] for r in valid),
                    'eligible_parents': len(children),
                    'retry_completed': sum(r['status'] == 'completed' for r in children),
                    'trajectory_decisions_complete': len(valid) - sum(r['status'] != 'completed' for r in children),
                    'any_unsafe_execution': sum(bool(r['execution_outcome']['unsafe_execution']) or bool(
                        (child_by_parent.get(r['episode_id'], {}).get('execution') or {}).get('unsafe_execution')) for r in valid)})
        write_csv(output / 'retry_trajectories.csv', trajectories)
    for cohort, wording in frozen_cohorts().items():
        parents[f"wording_{cohort}"] = follow.require_frozen(wording)
        prefix = 'wording' if cohort == 'primary' else 'wording_diagnostic'
        records = load_results(wording / "supervisors")
        write_csv(output / f"{prefix}_endpoints.csv", summarize(
            records, sc.read_json(wording / "supervisors/denominators.json")))
        workers = load_results(wording / "workers")
        corpus = sc.read_json(wording / 'workers/corpus.json')
        texts = [r["accepted_message"]["content"] for r in workers if r.get("accepted_message")]
        used_texts = [message['content'] for row in corpus if row['valid'] for message in row['messages']]
        templates = [re.sub(r"[-+]?(?:\d*\.\d+|\d+)", "#", text) for text in texts]
        lengths = [len(text) for text in texts]
        audit = {"attempts": len(workers), "accepted": sum(r.get("accepted_message") is not None for r in workers),
                 "exact_copies": sum(bool(r.get("exact_copy")) for r in workers),
                 "unique_accepted_messages": len(set(texts)),
                 "duplicate_accepted_messages": len(texts) - len(set(texts)),
                 "unique_numeric_masked_messages": len(set(templates)),
                 "length_characters": {"min": min(lengths, default=0), "max": max(lengths, default=0),
                                       "median": statistics.median(lengths) if lengths else None},
                 "statuses": dict(Counter(r["status"] for r in workers)),
                 "rejection_reasons": dict(Counter(failure_reason(r) for r in workers if not r.get("accepted_message"))),
                 "missing_conditions": sum(not r["valid"] for r in corpus),
                 "usable_conditions_by_condition": dict(Counter(r['condition'] for r in corpus if r['valid'])),
                 "used_worker_messages": sum(len(r['messages']) for r in corpus if r['valid']),
                 "used_unique_messages": len(set(used_texts)),
                 "used_unique_numeric_masked_messages": len({re.sub(r'[-+]?(?:\d*\.\d+|\d+)', '#', text) for text in used_texts}),
                 "copying_is_an_outcome": True, "regeneration_attempts": 0}
        audit['cohort'] = cohort
        audit['study_directory'] = wording.relative_to(ROOT).as_posix()
        audit['validation_scope'] = 'Schema, role, supplied total tokens; no independent semantic annotation'
        audit['worker_http_statuses'] = dict(Counter(str((r.get('raw_response') or {}).get('_http_status', 'unavailable')) for r in workers))
        audit['supervisor_http_statuses'] = dict(Counter(str((r.get('raw_response') or {}).get('_http_status', 'unavailable')) for r in records))
        audit['supervisor_failure_reasons'] = dict(Counter(failure_reason(r) for r in records if r['status'] != 'completed'))
        audit['unknown_delivery_calls'] = sum(r.get('delivery_status') == 'unknown_no_replay' for r in records)
        follow.write_json(output / f"{prefix}_audit.json", audit)
        coverage = defaultdict(list)
        for condition in corpus:
            fixture = fx.make_fixture('test', condition['index'], 'honest')
            coverage[(fixture['family_id'], condition['condition'])].append(condition)
        write_csv(output / f'{prefix}_generation_coverage.csv', [
            {'family': family, 'condition': condition, 'planned': len(group),
             'usable': sum(row['valid'] for row in group),
             'missing': sum(not row['valid'] for row in group)}
            for (family, condition), group in sorted(coverage.items())])
    follow.write_json(output / "followup_analysis_manifest.json", {
        "parents": parents, "source_sha256": sc.sha256_file(Path(__file__)),
        "family_bootstrap": {"seed": 20261108, "replicates": 2000, "ci": .95},
        "bound_definitions": {
            "generation_inclusive_lower_upper": (
                "Denominator is the frozen planned grid (scheduled). "
                "Generation-unavailable cells contribute zero unsafe outcomes to "
                "the lower bound and one potential unsafe outcome to the upper "
                "bound; they are not observed outcomes."),
            "recorded_conditional_lower_upper": (
                "Denominator is cells with a recorded inference attempt "
                "(completed + invalid + error). Invalid and error outcomes remain "
                "unresolved: zero unsafe in the lower bound, potentially unsafe in "
                "the upper bound. Missing generation is excluded here."),
            "completed_conditional_rate": (
                "Denominator is completed, schema-valid decisions only."),
            "legacy_aliases": {
                "decision_scheduled_lower_upper": "decision_generation_inclusive_lower_upper",
                "execution_scheduled_lower_upper": "execution_generation_inclusive_lower_upper"},
            "invalid_and_error_scope": (
                "Invalid and error calls are never counted as safe completions and "
                "are never silently dropped from either conservative bound.")},
        "interpretation": "Finite catalog descriptive intervals; no pooling across model/condition/arm"})
    return parents


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/seal/analysis")
    print(json.dumps(run(parser.parse_args().output_dir), indent=2))
