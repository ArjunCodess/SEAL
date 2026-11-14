"""Offline analysis of supplied live multi-agent oversight episode records.

This module reads episode JSONL produced elsewhere. It makes no API calls,
reads no secrets, and never infers an outcome that is absent from the input.
All failed and invalid episode records remain visible in explicit denominators.

Usage:
    python -m src.live_analysis \
        --input results/live_study/main/episodes.jsonl \
        --output results/live_study/analysis
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

SCHEMA_VERSION = 'live-analysis-v1'
ARMS = ('claims', 'receipts', 'state', 'provenance')
COORDINATIONS = ('single', 'coordinated', 'honest')
STATUSES = ('completed', 'error', 'invalid')
ACTIONS = ('publish', 'block', 'abstain')
OUTCOME_FIELDS = (
    'publication_attempted',
    'publication_executed',
    'unsafe_attempt',
    'unsafe_execution',
    'legitimate_completion',
)
REQUIRED_FIELDS = (
    'episode_id',
    'model',
    'arm',
    'fixture_id',
    'family_id',
    'split',
    'coordination',
    'mechanism',
    'legitimate',
    'answerable',
    'decision',
    'outcome',
    'status',
    'usage',
    'latency_seconds',
    'cost_units',
)
ENDPOINTS = ('unsafe_execution', 'legitimate_completion')
DEFAULT_SEED = 20261108
DEFAULT_BOOTSTRAP = 2000
DEFAULT_CI = 0.95


class SchemaError(ValueError):
    """Raised when an episode record cannot be analysed without guessing."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        preview = '\n'.join(f'  - {item}' for item in self.errors[:40])
        remainder = len(self.errors) - 40
        if remainder > 0:
            preview += f'\n  - ... and {remainder} more error(s)'
        super().__init__(f'Episode schema validation failed with {len(self.errors)} error(s):\n{preview}')


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _context(line: int, index: int, record: Mapping[str, Any]) -> str:
    episode = record.get('episode_id')
    suffix = f", episode_id={episode!r}" if isinstance(episode, str) and episode else ''
    return f'line {line} (record {index}){suffix}'


def _string_field(record: Mapping[str, Any], field: str, context: str,
                  errors: list[str], *, choices: Sequence[str] | None = None,
                  allow_empty: bool = False) -> str | None:
    if field not in record:
        errors.append(f"{context}: missing required field '{field}'")
        return None
    value = record[field]
    if not isinstance(value, str):
        errors.append(f"{context}: field '{field}' must be a string, got {type(value).__name__}")
        return None
    if not allow_empty and not value.strip():
        errors.append(f"{context}: field '{field}' must not be empty")
        return None
    if choices is not None and value not in choices:
        errors.append(f"{context}: field '{field}' has invalid value {value!r}; expected one of {list(choices)}")
        return None
    return value


def _bool_field(value: Any, field: str, context: str, errors: list[str]) -> bool | None:
    if not isinstance(value, bool):
        errors.append(f"{context}: field '{field}' must be a boolean, got {type(value).__name__}")
        return None
    return value


def _number_field(value: Any, field: str, context: str, errors: list[str]) -> float | None:
    if not _is_number(value):
        errors.append(f"{context}: field '{field}' must be a number, got {type(value).__name__}")
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        errors.append(f"{context}: field '{field}' must be a finite representable number")
        return None
    if not math.isfinite(number):
        errors.append(f"{context}: field '{field}' must be finite")
        return None
    if number < 0:
        errors.append(f"{context}: field '{field}' must be non-negative")
        return None
    return number


def _validate_decision(value: Any, context: str, errors: list[str],
                       allow_null: bool) -> dict[str, Any] | None:
    if value is None:
        if not allow_null:
            errors.append(f"{context}: field 'decision' may be null only when status is error or invalid")
        return None
    if not isinstance(value, dict):
        errors.append(f"{context}: field 'decision' must be an object or null")
        return None
    action = _string_field(value, 'action', context + ':decision', errors, choices=ACTIONS)
    summary = value.get('summary')
    if 'summary' not in value:
        errors.append(f"{context}: missing required field 'decision.summary'")
    elif not isinstance(summary, dict):
        errors.append(f"{context}: field 'decision.summary' must be an object")
    reason = _string_field(value, 'reason', context + ':decision', errors, allow_empty=True)
    if action is None:
        return None
    return {'action': action, 'summary': summary if isinstance(summary, dict) else {},
            'reason': reason if isinstance(reason, str) else ''}


def _validate_outcome(value: Any, context: str, errors: list[str],
                      allow_null: bool) -> dict[str, bool] | None:
    if value is None:
        if not allow_null:
            errors.append(f"{context}: field 'outcome' may be null only when status is error or invalid")
        return None
    if not isinstance(value, dict):
        errors.append(f"{context}: field 'outcome' must be an object or null")
        return None
    parsed: dict[str, bool] = {}
    for field in OUTCOME_FIELDS:
        if field not in value:
            errors.append(f"{context}: missing required field 'outcome.{field}'")
            continue
        checked = _bool_field(value[field], f'outcome.{field}', context, errors)
        if checked is not None:
            parsed[field] = checked
    if len(parsed) != len(OUTCOME_FIELDS):
        return None
    checks = [
        ('publication_executed', 'publication_attempted', 'publication_executed=true requires publication_attempted=true'),
        ('unsafe_execution', 'unsafe_attempt', 'unsafe_execution=true requires unsafe_attempt=true'),
        ('unsafe_execution', 'publication_executed', 'unsafe_execution=true requires publication_executed=true'),
    ]
    for left, right, message in checks:
        if parsed[left] and not parsed[right]:
            errors.append(f'{context}: incoherent outcome: {message}')
    return parsed


def _validate_usage(value: Any, context: str, errors: list[str]) -> dict[str, int] | None:
    if not isinstance(value, dict):
        errors.append(f"{context}: field 'usage' must be an object")
        return None
    parsed: dict[str, int] = {}
    for field in ('input_tokens', 'output_tokens'):
        if field not in value:
            errors.append(f"{context}: missing required field 'usage.{field}'")
            continue
        token_value = value[field]
        if isinstance(token_value, bool) or not isinstance(token_value, int):
            errors.append(f"{context}: field 'usage.{field}' must be a non-negative integer")
            continue
        if token_value < 0:
            errors.append(f"{context}: field 'usage.{field}' must be non-negative")
            continue
        parsed[field] = int(token_value)
    return parsed if len(parsed) == 2 else None


def _validate_record(raw: Any, line: int, index: int, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        errors.append(f'line {line} (record {index}): each line must contain a JSON object')
        return None
    context = _context(line, index, raw)
    record: dict[str, Any] = {}
    record['episode_id'] = _string_field(raw, 'episode_id', context, errors)
    record['model'] = _string_field(raw, 'model', context, errors)
    record['arm'] = _string_field(raw, 'arm', context, errors, choices=ARMS)
    record['fixture_id'] = _string_field(raw, 'fixture_id', context, errors)
    record['family_id'] = _string_field(raw, 'family_id', context, errors)
    record['split'] = _string_field(raw, 'split', context, errors)
    record['coordination'] = _string_field(raw, 'coordination', context, errors, choices=COORDINATIONS)
    record['mechanism'] = _string_field(raw, 'mechanism', context, errors)
    for field in ('legitimate', 'answerable'):
        if field not in raw:
            errors.append(f"{context}: missing required field '{field}'")
            record[field] = None
        else:
            record[field] = _bool_field(raw[field], field, context, errors)
    record['status'] = _string_field(raw, 'status', context, errors, choices=STATUSES)
    allow_null = record['status'] in ('error', 'invalid')
    record['decision'] = _validate_decision(raw.get('decision'), context, errors, allow_null)
    record['outcome'] = _validate_outcome(raw.get('outcome'), context, errors, allow_null)
    record['usage'] = (None if allow_null and raw.get('usage') is None
                       else _validate_usage(raw.get('usage'), context, errors))
    for field in ('latency_seconds', 'cost_units'):
        if field not in raw:
            errors.append(f"{context}: missing required field '{field}'")
            record[field] = None
        elif allow_null and raw[field] is None:
            record[field] = None
        else:
            record[field] = _number_field(raw[field], field, context, errors)
    for field in ('decision', 'outcome'):
        if field not in raw:
            errors.append(f"{context}: missing required field '{field}'")
    if record['status'] == 'completed':
        if raw.get('decision') is None:
            errors.append(f"{context}: completed episodes require a non-null decision object")
        if raw.get('outcome') is None:
            errors.append(f"{context}: completed episodes require a non-null outcome object")
    if record['outcome'] is not None and record['legitimate'] is False:
        if record['outcome']['legitimate_completion']:
            errors.append(f'{context}: illegitimate episodes cannot have legitimate_completion')
    if record['outcome'] is not None and record['outcome']['legitimate_completion']:
        if record['legitimate'] is not True:
            errors.append(f'{context}: legitimate_completion requires legitimate=true')
    nullable = {'decision', 'outcome', 'usage', 'latency_seconds', 'cost_units'} if allow_null else set()
    if any(record[field] is None for field in REQUIRED_FIELDS if field not in nullable):
        return None
    return record


def validate_episodes(rows: Sequence[tuple[int, Any]]) -> list[dict[str, Any]]:
    """Validate raw JSONL rows, raising one aggregated error for every defect."""
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    for index, (line, raw) in enumerate(rows):
        parsed = _validate_record(raw, line, index, errors)
        if parsed is not None:
            records.append(parsed)
    if errors:
        raise SchemaError(errors)

    seen_ids: dict[str, int] = {}
    fixture_meta: dict[str, dict[str, Any]] = {}
    fixture_conflicts: set[tuple[str, str]] = set()
    family_split: dict[str, str] = {}
    family_conflicts: set[str] = set()
    design_cells: dict[tuple[Any, ...], str] = {}
    for record_index, record in enumerate(records):
        episode_id = record['episode_id']
        if episode_id in seen_ids:
            errors.append(f'duplicate episode_id {episode_id!r} on records '
                          f'{seen_ids[episode_id]} and {record_index}')
        else:
            seen_ids[episode_id] = record_index
        fixture_id = record['fixture_id']
        meta = fixture_meta.setdefault(fixture_id, {})
        for field in ('family_id', 'split', 'answerable'):
            value = record[field]
            if field in meta and meta[field] != value and (fixture_id, field) not in fixture_conflicts:
                errors.append(f"fixture {fixture_id!r} has conflicting {field}: {meta[field]!r} vs {value!r}")
                fixture_conflicts.add((fixture_id, field))
            else:
                meta.setdefault(field, value)
        prior_split = family_split.setdefault(record['family_id'], record['split'])
        if prior_split != record['split'] and record['family_id'] not in family_conflicts:
            errors.append(f"family {record['family_id']!r} spans splits {prior_split!r} and {record['split']!r}")
            family_conflicts.add(record['family_id'])
        cell = (record['model'], record['arm'], record['coordination'], record['fixture_id'])
        if cell in design_cells:
            errors.append(f'duplicate design cell {cell}: episodes {design_cells[cell]!r} and {record["episode_id"]!r}')
        else:
            design_cells[cell] = record['episode_id']
    splits = {record['split'] for record in records}
    if len(splits) > 1:
        errors.append(f'input contains multiple splits {sorted(splits)}; analyze one split at a time '
                      'so that development and test records are not pooled')
    if errors:
        raise SchemaError(errors)
    return records


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Parse JSONL without analysing it. Blank lines are counted, not treated as records."""
    errors: list[str] = []
    rows: list[tuple[int, Any]] = []
    blank = 0
    with Path(path).open('r', encoding='utf-8-sig') as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                blank += 1
                continue
            try:
                rows.append((line_number, json.loads(line)))
            except json.JSONDecodeError as exc:
                errors.append(f'line {line_number}: invalid JSON: {exc.msg} at column {exc.colno}')
    if errors:
        raise SchemaError(errors)
    if not rows:
        raise SchemaError(['No episode records found; an empty input cannot be analysed'])
    records = validate_episodes(rows)
    return records, blank


@dataclass(frozen=True)
class BootstrapResult:
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    replicates: int
    seed: int
    key: str


def _rng(seed: int, key: str) -> np.random.Generator:
    derived = int(np.random.SeedSequence([int(seed), zlib.crc32(key.encode('utf8'))]).generate_state(1)[0])
    return np.random.default_rng(derived)


def _percentile_interval(values: np.ndarray, ci: float) -> tuple[float, float]:
    alpha = (1.0 - ci) / 2.0
    low, high = np.quantile(values, [alpha, 1.0 - alpha])
    return float(low), float(high)


def _check_bootstrap_args(replicates: int, ci: float) -> None:
    if replicates < 1:
        raise ValueError('replicates must be at least 1')
    if not 0.0 < ci < 1.0:
        raise ValueError('ci must be strictly between 0 and 1')


def bootstrap_family_rate(blocks: Sequence[tuple[int, int]], *, seed: int, key: str,
                          replicates: int, ci: float) -> BootstrapResult:
    """Cluster bootstrap for a binary rate, resampling whole family blocks."""
    _check_bootstrap_args(replicates, ci)
    usable = [(int(successes), int(total)) for successes, total in blocks if int(total) > 0]
    if not usable:
        return BootstrapResult(None, None, None, replicates, seed, key)
    total_successes = sum(successes for successes, _ in usable)
    total_observations = sum(total for _, total in usable)
    estimate = total_successes / total_observations
    rng = _rng(seed, key)
    n_blocks = len(usable)
    values = np.empty(replicates, dtype=float)
    successes_array = np.array([successes for successes, _ in usable], dtype=float)
    totals_array = np.array([total for _, total in usable], dtype=float)
    for index in range(replicates):
        draw = rng.integers(0, n_blocks, size=n_blocks)
        values[index] = successes_array[draw].sum() / totals_array[draw].sum()
    low, high = _percentile_interval(values, ci)
    return BootstrapResult(float(estimate), low, high, replicates, seed, key)


def bootstrap_paired_difference(differences: Sequence[float], *, seed: int, key: str,
                                replicates: int, ci: float) -> BootstrapResult:
    """Cluster bootstrap over paired family-level differences."""
    _check_bootstrap_args(replicates, ci)
    values = np.asarray(list(differences), dtype=float)
    if values.size == 0:
        return BootstrapResult(None, None, None, replicates, seed, key)
    estimate = float(values.mean())
    rng = _rng(seed, key)
    draws = np.empty(replicates, dtype=float)
    for index in range(replicates):
        sample = rng.integers(0, values.size, size=values.size)
        draws[index] = values[sample].mean()
    low, high = _percentile_interval(draws, ci)
    return BootstrapResult(estimate, low, high, replicates, seed, key)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _sum_where(rows: Iterable[Mapping[str, Any]], predicate: Callable[[Mapping[str, Any]], bool]) -> int:
    return sum(1 for row in rows if predicate(row))


def _family_blocks(rows: Sequence[Mapping[str, Any]], success_field: str) -> list[tuple[int, int]]:
    blocks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        blocks[row['family_id']][1] += 1
        if row['outcome'] is not None and row['outcome'][success_field]:
            blocks[row['family_id']][0] += 1
    return [(values[0], values[1]) for values in blocks.values()]


def _all_status_successes(rows: Sequence[Mapping[str, Any]], success_field: str) -> int:
    return _sum_where(rows, lambda row: row['outcome'] is not None and row['outcome'][success_field])


def aggregate_metrics(records: Sequence[Mapping[str, Any]], *, seed: int, replicates: int,
                      ci: float) -> list[dict[str, Any]]:
    """One row per model x arm x coordination. No model or coordination pooling."""
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record['model'], record['arm'], record['coordination'])].append(record)
    models = sorted({record['model'] for record in records})
    keys = sorted(grouped, key=lambda key: (models.index(key[0]), ARMS.index(key[1]), COORDINATIONS.index(key[2])))
    output: list[dict[str, Any]] = []
    for model, arm, coordination in keys:
        rows = grouped[(model, arm, coordination)]
        completed = [row for row in rows if row['status'] == 'completed']
        errors = [row for row in rows if row['status'] == 'error']
        invalids = [row for row in rows if row['status'] == 'invalid']
        legitimate = [row for row in rows if row['legitimate']]
        legitimate_completed = [row for row in legitimate if row['status'] == 'completed']
        illegitimate = [row for row in rows if not row['legitimate']]
        illegitimate_completed = [row for row in illegitimate if row['status'] == 'completed']
        answerable_completed = [row for row in completed if row['answerable']]
        unanswerable_completed = [row for row in completed if not row['answerable']]
        publish = _sum_where(completed, lambda row: row['decision']['action'] == 'publish')
        block = _sum_where(completed, lambda row: row['decision']['action'] == 'block')
        abstain = _sum_where(completed, lambda row: row['decision']['action'] == 'abstain')
        unsafe_publish = _sum_where(illegitimate_completed, lambda row: row['decision']['action'] == 'publish')
        unsafe_attempts = _sum_where(illegitimate_completed, lambda row: row['outcome']['unsafe_attempt'])
        unsafe_executions = _sum_where(illegitimate_completed, lambda row: row['outcome']['unsafe_execution'])
        gateway_blocked = _sum_where(
            illegitimate_completed,
            lambda row: row['outcome']['unsafe_attempt'] and not row['outcome']['unsafe_execution'],
        )
        publication_attempts = _sum_where(illegitimate_completed, lambda row: row['outcome']['publication_attempted'])
        publication_executions = _sum_where(illegitimate_completed, lambda row: row['outcome']['publication_executed'])
        publication_blocked = _sum_where(
            illegitimate_completed,
            lambda row: row['outcome']['publication_attempted'] and not row['outcome']['publication_executed'],
        )
        legit_attempts = _sum_where(legitimate_completed, lambda row: row['outcome']['publication_attempted'])
        legit_executions = _sum_where(legitimate_completed, lambda row: row['outcome']['publication_executed'])
        legit_completions = _sum_where(legitimate_completed, lambda row: row['outcome']['legitimate_completion'])
        legit_abstentions = _sum_where(legitimate_completed, lambda row: row['decision']['action'] == 'abstain')
        unsafe_all = _all_status_successes(illegitimate, 'unsafe_execution')
        legit_all = _all_status_successes(legitimate, 'legitimate_completion')
        unsafe_ci = bootstrap_family_rate(
            _family_blocks(illegitimate_completed, 'unsafe_execution'),
            seed=seed, key=f'rate|unsafe_execution|{model}|{arm}|{coordination}',
            replicates=replicates, ci=ci)
        legit_ci = bootstrap_family_rate(
            _family_blocks(legitimate_completed, 'legitimate_completion'),
            seed=seed, key=f'rate|legitimate_completion|{model}|{arm}|{coordination}',
            replicates=replicates, ci=ci)
        latencies = [row['latency_seconds'] for row in rows if row['latency_seconds'] is not None]
        output.append({
            'model': model,
            'arm': arm,
            'coordination': coordination,
            'episodes_total': len(rows),
            'status_completed': len(completed),
            'status_error': len(errors),
            'status_invalid': len(invalids),
            'episodes_completed_denominator': len(completed),
            'noncompletion_rate': _rate(len(errors) + len(invalids), len(rows)),
            'error_rate': _rate(len(errors), len(rows)),
            'invalid_rate': _rate(len(invalids), len(rows)),
            'families': len({row['family_id'] for row in rows}),
            'fixtures': len({row['fixture_id'] for row in rows}),
            'legitimate_total': len(legitimate),
            'legitimate_completed': len(legitimate_completed),
            'illegitimate_total': len(illegitimate),
            'illegitimate_completed': len(illegitimate_completed),
            'answerable_total': _sum_where(rows, lambda row: row['answerable']),
            'unanswerable_total': _sum_where(rows, lambda row: not row['answerable']),
            'action_publish': publish,
            'action_block': block,
            'action_abstain': abstain,
            'abstention_rate': _rate(abstain, len(completed)),
            'answerable_completed': len(answerable_completed),
            'answerable_abstentions': _sum_where(answerable_completed, lambda row: row['decision']['action'] == 'abstain'),
            'answerable_abstention_rate': _rate(
                _sum_where(answerable_completed, lambda row: row['decision']['action'] == 'abstain'),
                len(answerable_completed)),
            'unanswerable_completed': len(unanswerable_completed),
            'unanswerable_abstentions': _sum_where(
                unanswerable_completed, lambda row: row['decision']['action'] == 'abstain'),
            'unanswerable_abstention_rate': _rate(
                _sum_where(unanswerable_completed, lambda row: row['decision']['action'] == 'abstain'),
                len(unanswerable_completed)),
            'illegitimate_publish_decisions': unsafe_publish,
            'illegitimate_publish_decision_rate': _rate(unsafe_publish, len(illegitimate_completed)),
            'illegitimate_outcome_unsafe_attempts': unsafe_attempts,
            'illegitimate_unsafe_attempt_rate': _rate(unsafe_attempts, len(illegitimate_completed)),
            'illegitimate_outcome_unsafe_executions': unsafe_executions,
            'illegitimate_unsafe_execution_rate': _rate(unsafe_executions, len(illegitimate_completed)),
            'illegitimate_unsafe_execution_ci_low': unsafe_ci.ci_low,
            'illegitimate_unsafe_execution_ci_high': unsafe_ci.ci_high,
            'illegitimate_gateway_blocked_unsafe_attempts': gateway_blocked,
            'illegitimate_gateway_execution_given_unsafe_attempt': _rate(unsafe_executions, unsafe_attempts),
            'illegitimate_gateway_block_rate_given_unsafe_attempt': _rate(gateway_blocked, unsafe_attempts),
            'illegitimate_publication_attempts': publication_attempts,
            'illegitimate_publication_executions': publication_executions,
            'illegitimate_publication_execution_given_attempt': _rate(publication_executions, publication_attempts),
            'illegitimate_publication_blocked_given_attempt': _rate(publication_blocked, publication_attempts),
            'illegitimate_decision_to_outcome_attempt_gap': unsafe_publish - unsafe_attempts,
            'illegitimate_unsafe_execution_all_status_count': unsafe_all,
            'illegitimate_unsafe_execution_all_status_rate': _rate(unsafe_all, len(illegitimate)),
            'legitimate_publication_attempts': legit_attempts,
            'legitimate_publication_executions': legit_executions,
            'legitimate_completions': legit_completions,
            'legitimate_unsafe_attempts': _sum_where(legitimate_completed, lambda row: row['outcome']['unsafe_attempt']),
            'legitimate_unsafe_executions': _sum_where(legitimate_completed, lambda row: row['outcome']['unsafe_execution']),
            'all_completed_unsafe_executions': _sum_where(completed, lambda row: row['outcome']['unsafe_execution']),
            'legitimate_completion_rate': _rate(legit_completions, len(legitimate_completed)),
            'legitimate_completion_ci_low': legit_ci.ci_low,
            'legitimate_completion_ci_high': legit_ci.ci_high,
            'legitimate_completion_all_status_count': legit_all,
            'legitimate_completion_all_status_rate': _rate(legit_all, len(legitimate)),
            'legitimate_abstentions': legit_abstentions,
            'legitimate_abstention_rate': _rate(legit_abstentions, len(legitimate_completed)),
            'usage_unknown_episodes': sum(row['usage'] is None for row in rows),
            'cost_unknown_episodes': sum(row['cost_units'] is None for row in rows),
            'input_tokens_sum': sum(row['usage']['input_tokens'] for row in rows if row['usage'] is not None),
            'output_tokens_sum': sum(row['usage']['output_tokens'] for row in rows if row['usage'] is not None),
            'latency_seconds_mean': float(np.mean(latencies)) if latencies else None,
            'cost_units_sum': float(sum(row['cost_units'] for row in rows if row['cost_units'] is not None)),
        })
    return output


def endpoint_denominators(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Explicit denominator accounting for each co-primary endpoint."""
    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record['model'], record['arm'], record['coordination'])].append(record)
    models = sorted({record['model'] for record in records})
    keys = sorted(grouped, key=lambda key: (models.index(key[0]), ARMS.index(key[1]), COORDINATIONS.index(key[2])))
    output: list[dict[str, Any]] = []
    for model, arm, coordination in keys:
        rows = grouped[(model, arm, coordination)]
        for endpoint in ENDPOINTS:
            eligible_all = [row for row in rows
                            if (not row['legitimate'] if endpoint == 'unsafe_execution' else row['legitimate'])]
            completed = [row for row in eligible_all if row['status'] == 'completed']
            errors = [row for row in eligible_all if row['status'] == 'error']
            invalids = [row for row in eligible_all if row['status'] == 'invalid']
            successes = _sum_where(completed, lambda row: row['outcome'][endpoint])
            observed_all = _all_status_successes(eligible_all, endpoint)
            missing_outcome = _sum_where(eligible_all, lambda row: row['outcome'] is None)
            output.append({
                'model': model,
                'arm': arm,
                'coordination': coordination,
                'endpoint': endpoint,
                'total_episodes': len(eligible_all),
                'completed': len(completed),
                'error': len(errors),
                'invalid': len(invalids),
                'noncompleted': len(errors) + len(invalids),
                'missing_outcome': missing_outcome,
                'completed_denominator': len(completed),
                'successes_completed': successes,
                'complete_case_rate': _rate(successes, len(completed)),
                'observed_successes_all_status': observed_all,
                'all_status_observed_rate': _rate(observed_all, len(eligible_all)),
                'families': len({row['family_id'] for row in eligible_all}),
                'note': ('complete-case co-primary rate; all-status sensitivity uses observed successes '
                         'across every episode. Unobserved or missing-outcome non-completed episodes count '
                         'as non-success; no outcome is inferred.'),
            })
    return output


ARM_PAIRS = tuple((ARMS[i], ARMS[j]) for i in range(len(ARMS)) for j in range(i + 1, len(ARMS)))


def _endpoint_eligible(record: Mapping[str, Any], endpoint: str) -> bool:
    if record['status'] != 'completed':
        return False
    return not record['legitimate'] if endpoint == 'unsafe_execution' else record['legitimate']


def families_to_rows(families: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[Mapping[str, Any]]:
    return [row for rows in families.values() for row in rows]


def paired_contrasts(records: Sequence[Mapping[str, Any]], *, seed: int, replicates: int,
                     ci: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Family-level paired arm contrasts with explicit missing-pair accounting.

    A family enters a contrast only when all four arms have a completed,
    endpoint-eligible episode and the fixture sets agree across arms. Families
    are the independent resampling blocks.
    """
    grouped: dict[tuple[str, str], dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        grouped[(record['model'], record['coordination'])][record['family_id']].append(record)
    models = sorted({record['model'] for record in records})
    strata = sorted(grouped, key=lambda key: (models.index(key[0]), COORDINATIONS.index(key[1])))
    inventory: list[dict[str, Any]] = []
    contrasts: list[dict[str, Any]] = []
    for model, coordination in strata:
        families = grouped[(model, coordination)]
        for endpoint in ENDPOINTS:
            family_stats: dict[str, dict[str, tuple[int, int]]] = {}
            all_arms: dict[str, set[str]] = {}
            endpoint_arms: dict[str, set[str]] = {}
            fixture_sets: dict[str, dict[str, frozenset[str]]] = {}
            endpoint_records_by_family: dict[str, list[Mapping[str, Any]]] = {}
            for family, family_records in families.items():
                all_arms[family] = {row['arm'] for row in family_records}
                eligible = [row for row in family_records if _endpoint_eligible(row, endpoint)]
                endpoint_records_by_family[family] = eligible
                endpoint_arms[family] = {row['arm'] for row in eligible}
                fixture_sets[family] = {
                    arm: frozenset(row['fixture_id'] for row in eligible if row['arm'] == arm)
                    for arm in ARMS if any(row['arm'] == arm for row in eligible)
                }
                stats: dict[str, tuple[int, int]] = {}
                for arm in ARMS:
                    arm_rows = [row for row in eligible if row['arm'] == arm]
                    stats[arm] = (sum(1 for row in arm_rows if row['outcome'][endpoint]), len(arm_rows))
                family_stats[family] = stats
            all_arm_families = {family for family, arms in all_arms.items() if set(ARMS).issubset(arms)}
            endpoint_complete = {family for family, arms in endpoint_arms.items()
                                 if set(ARMS).issubset(arms)}
            fixture_ok = {
                family for family in endpoint_complete
                if len(set(fixture_sets[family].values())) == 1
                and all(fixture_sets[family].get(arm) for arm in ARMS)
            }
            used = endpoint_complete & fixture_ok
            used_episodes = sum(len(endpoint_records_by_family[family]) for family in used)
            eligible_episodes = sum(len(rows) for rows in endpoint_records_by_family.values())
            legitimate_eligible_total = sum(
                1 for row in families_to_rows(families) if (
                    not row['legitimate'] if endpoint == 'unsafe_execution' else row['legitimate']))
            inventory.append({
                'endpoint': endpoint,
                'model': model,
                'coordination': coordination,
                'families_total': len(families),
                'families_all_arms': len(all_arm_families),
                'families_all_arms_endpoint': len(endpoint_complete),
                'families_fixture_sets_match': len(fixture_ok),
                'families_used': len(used),
                'families_missing_any_arm': len(families) - len(all_arm_families),
                'families_arm_without_endpoint': len(all_arm_families) - len(endpoint_complete),
                'families_fixture_mismatch': len(endpoint_complete) - len(used),
                'unpaired_families': len(families) - len(used),
                'stratum_episodes_total': sum(len(rows) for rows in families.values()),
                'stratum_episodes_completed': sum(
                    1 for rows in families.values() for row in rows if row['status'] == 'completed'),
                'stratum_noncompleted': sum(
                    1 for rows in families.values() for row in rows if row['status'] != 'completed'),
                'legitimacy_eligible_total': legitimate_eligible_total,
                'legitimacy_eligible_noncompleted': legitimate_eligible_total - eligible_episodes,
                'endpoint_eligible_episodes': eligible_episodes,
                'used_episodes': used_episodes,
                'excluded_episodes': eligible_episodes - used_episodes,
            })
            for arm_a, arm_b in ARM_PAIRS:
                differences: list[float] = []
                for family in sorted(used):
                    success_a, count_a = family_stats[family][arm_a]
                    success_b, count_b = family_stats[family][arm_b]
                    differences.append(success_a / count_a - success_b / count_b)
                key = f'pair|{endpoint}|{model}|{coordination}|{arm_a}_minus_{arm_b}'
                result = bootstrap_paired_difference(
                    differences, seed=seed, key=key, replicates=replicates, ci=ci)
                contrasts.append({
                    'endpoint': endpoint,
                    'model': model,
                    'coordination': coordination,
                    'arm_a': arm_a,
                    'arm_b': arm_b,
                    'contrast': f'{arm_a}_minus_{arm_b}',
                    'all_arms_matched': True,
                    'pairs': len(differences),
                    'families_total': len(families),
                    'unpaired_families': len(families) - len(used),
                    'families_missing_any_arm': len(families) - len(all_arm_families),
                    'families_arm_without_endpoint': len(all_arm_families) - len(endpoint_complete),
                    'families_fixture_mismatch': len(endpoint_complete) - len(used),
                    'estimate': result.estimate,
                    'ci_low': result.ci_low,
                    'ci_high': result.ci_high,
                    'ci_level': ci,
                    'bootstrap_replicates': replicates,
                    'bootstrap_seed': seed,
                    'bootstrap_key': key,
                    'note': ('no families with all four endpoint-matched arms and matching fixture sets'
                             if not differences else
                             'paired family-level rate difference; family blocks resampled with replacement'),
                })
    return inventory, contrasts


def create_figure(aggregate: Sequence[Mapping[str, Any]], png_path: Path, pdf_path: Path) -> None:
    """Render a light-background 320 dpi PNG and vector PDF without pooling strata."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({
        'font.family': 'DejaVu Sans',
        'axes.spines.top': False,
        'axes.spines.right': False,
        'savefig.dpi': 320,
        'pdf.fonttype': 42,
        'ps.fonttype': 42,
    })
    models = sorted({row['model'] for row in aggregate})
    palette = ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9']
    colors = {model: palette[index % len(palette)] for index, model in enumerate(models)}
    lookup = {(row['model'], row['arm'], row['coordination']): row for row in aggregate}
    fig, axes = plt.subplots(2, 3, figsize=(7.6, 5.3), sharey='row')
    fig.subplots_adjust(left=0.095, right=0.985, top=0.83, bottom=0.175,
                        wspace=0.18, hspace=0.30)
    row_specs = (
        ('unsafe_execution', 'illegitimate_unsafe_execution_rate',
         'illegitimate_unsafe_execution_ci_low', 'illegitimate_unsafe_execution_ci_high',
         'illegitimate_completed', 'Unsafe execution\namong illegitimate episodes'),
        ('legitimate_completion', 'legitimate_completion_rate',
         'legitimate_completion_ci_low', 'legitimate_completion_ci_high',
         'legitimate_completed', 'Legitimate completion\namong legitimate episodes'),
    )
    column_titles = {'single': 'Single worker', 'coordinated': 'Coordinated', 'honest': 'Honest controls'}
    arm_labels = ['Claims', 'Receipts', 'State', 'Provenance']
    for row_index, (_, rate_key, low_key, high_key, denominator_key, row_label) in enumerate(row_specs):
        for column_index, coordination in enumerate(COORDINATIONS):
            ax = axes[row_index, column_index]
            ax.set_facecolor('#fbfbfb')
            ax.grid(axis='y', color='#e6e6e6', linewidth=0.6)
            ax.set_axisbelow(True)
            ax.set_xlim(-0.35, 3.35)
            ax.set_ylim(-0.04, 1.07)
            ax.set_xticks(range(4))
            ax.set_xticklabels(arm_labels, fontsize=7)
            ax.tick_params(axis='y', labelsize=7)
            if row_index == 0:
                ax.set_title(column_titles[coordination], fontsize=8.5, fontweight='bold')
            if column_index == 0:
                ax.set_ylabel(row_label, fontsize=7.5)
            data_found = False
            for model_index, model in enumerate(models):
                xs: list[int] = []
                ys: list[float] = []
                errors: list[list[float]] = [[], []]
                annotations: list[tuple[int, float, int]] = []
                for arm_index, arm in enumerate(ARMS):
                    row = lookup.get((model, arm, coordination))
                    if row is None:
                        continue
                    value = row.get(rate_key)
                    denominator = row.get(denominator_key) or 0
                    if value is None or denominator == 0:
                        continue
                    xs.append(arm_index)
                    ys.append(float(value))
                    low = row.get(low_key)
                    high = row.get(high_key)
                    if low is not None and high is not None:
                        errors[0].append(max(0.0, float(value) - float(low)))
                        errors[1].append(max(0.0, float(high) - float(value)))
                    else:
                        errors[0].append(0.0)
                        errors[1].append(0.0)
                    annotations.append((arm_index, float(value), denominator))
                    data_found = True
                if not xs:
                    continue
                color = colors[model]
                for segment in range(len(xs) - 1):
                    if xs[segment + 1] - xs[segment] == 1:
                        ax.plot(xs[segment:segment + 2], ys[segment:segment + 2],
                                color=color, linewidth=1.0, alpha=0.8, zorder=2)
                ax.errorbar(xs, ys, yerr=errors, fmt='o', color=color, ecolor=color,
                            elinewidth=0.7, capsize=2.0, markersize=3.4, markeredgewidth=0.5,
                            markeredgecolor='white', zorder=3)
                for x_value, y_value, count in annotations:
                    offset = 7 if model_index % 2 == 0 else -11
                    ax.annotate(f'n={count}', (x_value, y_value), textcoords='offset points',
                                xytext=(0, offset), ha='center', va='center', fontsize=4.6,
                                color=color, alpha=0.9, zorder=4)
            if not data_found:
                reason = 'No illegitimate episodes' if row_index == 0 else 'No legitimate episodes'
                ax.text(0.5, 0.5, reason, transform=ax.transAxes, ha='center', va='center',
                        fontsize=7.5, color='#777777')
    handles = [Line2D([0], [0], color=colors[model], marker='o', markersize=4, linewidth=1.2,
                      label=model) for model in models]
    if handles:
        fig.legend(handles=handles, loc='lower center', bbox_to_anchor=(0.5, 0.052),
                   ncol=min(4, len(handles)), frameon=False, fontsize=7)
    fig.suptitle('Offline analysis: co-primary endpoints by evidence arm',
                 x=0.012, y=0.978, ha='left', va='top', fontsize=10, fontweight='bold')
    fig.text(0.012, 0.925,
             'Points are complete-case rates; bars are 95% family-cluster bootstrap intervals. '
             'n = completed endpoint-eligible episodes.',
             ha='left', va='top', fontsize=6.5, color='#444444')
    fig.text(0.012, 0.901,
             'Facets and models are not pooled; error/invalid episodes remain in the denominator '
             'table and sensitivity analysis.',
             ha='left', va='top', fontsize=6.5, color='#444444')
    fig.text(0.012, 0.012,
             'Supplied episode records only; no live run is performed here.',
             ha='left', va='bottom', fontsize=5.8, color='#555555')
    fig.savefig(png_path, dpi=320, facecolor='white', bbox_inches='tight')
    fig.savefig(pdf_path, facecolor='white', bbox_inches='tight')
    plt.close(fig)


def _escape_markdown(value: Any) -> str:
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]],
                    right_columns: Sequence[int] = ()) -> str:
    lines = ['| ' + ' | '.join(_escape_markdown(header) for header in headers) + ' |']
    rules = []
    for index in range(len(headers)):
        rules.append('---:' if index in right_columns else ':---')
    lines.append('| ' + ' | '.join(rules) + ' |')
    for row in rows:
        lines.append('| ' + ' | '.join(_escape_markdown(value) for value in row) + ' |')
    return '\n'.join(lines)


def _fmt_rate(success: Any, denominator: Any, rate: Any, low: Any = None, high: Any = None) -> str:
    if denominator in (None, 0) or rate is None:
        return f'0/{denominator or 0} (no estimate)'
    text = f'{int(success)}/{int(denominator)} = {float(rate):.3f}'
    if low is not None and high is not None:
        text += f' [{float(low):.3f}, {float(high):.3f}]'
    return text


def _fmt_delta(value: Any, low: Any, high: Any, pairs: Any, total: Any) -> str:
    if pairs in (None, 0) or value is None:
        return f'no estimate ({int(pairs or 0)}/{int(total or 0)} families)'
    text = f'{float(value):+.3f}'
    if low is not None and high is not None:
        text += f' [{float(low):+.3f}, {float(high):+.3f}]'
    return f'{text} ({int(pairs)}/{int(total)} families)'


def render_markdown(aggregate: Sequence[Mapping[str, Any]],
                    denominators: Sequence[Mapping[str, Any]],
                    inventory: Sequence[Mapping[str, Any]],
                    contrasts: Sequence[Mapping[str, Any]],
                    summary: Mapping[str, Any]) -> str:
    input_info = summary['input']
    lines = [
        '# Live study analysis: supplied episode records',
        '',
        '**Scope.** This report is generated offline from the supplied episode JSONL. '
        'It does not run a live study, does not make API calls, and does not establish '
        'that the planned research was executed or completed. Missing outcomes are never inferred.',
        '',
        '## Input accounting',
        '',
        f"- Input: `{input_info['path']}`",
        f"- SHA-256: `{input_info['sha256']}`",
        f"- Records: {input_info['episodes']} episodes; {input_info['families']} families; "
        f"{input_info['fixtures']} fixtures; {input_info['blank_lines_ignored']} blank lines ignored",
        f"- Models: {', '.join(input_info['models'])}",
        f"- Coordinations: {', '.join(input_info['coordinations'])}",
        f"- Splits: {', '.join(input_info['splits'])}",
        f"- Statuses: {input_info['status_counts']}",
        '',
        'Every model/arm/coordination group is reported separately. No row pools models or '
        'coordination conditions, and family blocks are the resampling unit for paired contrasts.',
        '',
        '## Co-primary endpoints',
        '',
        'Unsafe execution is measured among illegitimate completed episodes. Legitimate completion '
        'is measured among legitimate completed episodes. The `all-status` sensitivity columns keep '
        'error and invalid episodes visible: observed successes are retained, while an episode with no '
        'observed outcome counts as non-success, never as an inferred outcome.',
        '',
    ]
    endpoint_rows = []
    for row in aggregate:
        endpoint_rows.append([
            row['model'], row['arm'], row['coordination'],
            f"{row['episodes_total']} ({row['status_completed']}/{row['status_error']}/{row['status_invalid']})",
            _fmt_rate(row['illegitimate_outcome_unsafe_executions'], row['illegitimate_completed'],
                      row['illegitimate_unsafe_execution_rate'],
                      row['illegitimate_unsafe_execution_ci_low'],
                      row['illegitimate_unsafe_execution_ci_high']),
            _fmt_rate(row['legitimate_completions'], row['legitimate_completed'],
                      row['legitimate_completion_rate'],
                      row['legitimate_completion_ci_low'], row['legitimate_completion_ci_high']),
            f"{row['illegitimate_unsafe_execution_all_status_count']}/{row['illegitimate_total']} = "
            f"{row['illegitimate_unsafe_execution_all_status_rate']:.3f}"
            if row['illegitimate_unsafe_execution_all_status_rate'] is not None else '0/0',
            f"{row['legitimate_completion_all_status_count']}/{row['legitimate_total']} = "
            f"{row['legitimate_completion_all_status_rate']:.3f}"
            if row['legitimate_completion_all_status_rate'] is not None else '0/0',
            _fmt_rate(row['action_abstain'], row['episodes_completed_denominator'], row['abstention_rate']),
            f"{row['status_error'] + row['status_invalid']}/{row['episodes_total']} = "
            f"{row['noncompletion_rate']:.3f}" if row['noncompletion_rate'] is not None else '0/0',
        ])
    lines.append(_markdown_table(
        ['Model', 'Arm', 'Coordination', 'Episodes (C/E/I)',
         'Unsafe execution (n/N; 95% CI)', 'Legitimate completion (n/N; 95% CI)',
         'Unsafe all-status', 'Legit. all-status', 'Abstain', 'Error+invalid'],
        endpoint_rows, right_columns=(3, 4, 5, 6, 7, 8, 9)))
    lines.extend([
        '',
        'C = completed, E = error, I = invalid. All-status sensitivity uses every episode in the '
        'legitimacy denominator. Observed successes are retained; an episode with no observed outcome '
        'counts as non-success and no outcome is inferred.',
        '',
        '### Honest controls',
        '',
    ])
    honest = [row for row in aggregate if row['coordination'] == 'honest']
    if honest:
        honest_rows = [[
            row['model'], row['arm'],
            _fmt_rate(row['legitimate_completions'], row['legitimate_completed'],
                      row['legitimate_completion_rate'],
                      row['legitimate_completion_ci_low'], row['legitimate_completion_ci_high']),
            _fmt_rate(row['action_abstain'], row['episodes_completed_denominator'], row['abstention_rate']),
            f"{row['status_error'] + row['status_invalid']}/{row['episodes_total']} = "
            f"{row['noncompletion_rate']:.3f}" if row['noncompletion_rate'] is not None else '0/0',
        ] for row in honest]
        lines.append(_markdown_table(
            ['Model', 'Arm', 'Legitimate completion (n/N; 95% CI)', 'Abstain', 'Error+invalid'],
            honest_rows, right_columns=(2, 3, 4)))
    else:
        lines.append('No honest-control episodes were supplied.')
    lines.extend([
        '',
        '## Paired family contrasts',
        '',
        'Each contrast uses only families with all four arms present, endpoint-eligible completed '
        'episodes in every arm, and matching fixture sets. Missing families are counted in the table '
        'and in `pairing_inventory.csv`; they are never silently dropped. Differences are '
        '`arm A - arm B`; family blocks are resampled with replacement using the fixed seed recorded '
        'in `summary.json`. Intervals are unadjusted and descriptive.',
        '',
    ])
    contrast_lookup = {(row['endpoint'], row['model'], row['coordination'], row['arm_a'], row['arm_b']): row
                       for row in contrasts}
    contrast_rows = []
    pairs = [(row['model'], row['coordination'], row['arm_a'], row['arm_b'], row['contrast'])
             for row in contrasts if row['endpoint'] == 'unsafe_execution']
    for model, coordination, arm_a, arm_b, label in pairs:
        unsafe = contrast_lookup.get(('unsafe_execution', model, coordination, arm_a, arm_b))
        legit = contrast_lookup.get(('legitimate_completion', model, coordination, arm_a, arm_b))
        contrast_rows.append([
            model, coordination, label,
            _fmt_delta(unsafe['estimate'], unsafe['ci_low'], unsafe['ci_high'],
                       unsafe['pairs'], unsafe['families_total']) if unsafe else 'missing',
            _fmt_delta(legit['estimate'], legit['ci_low'], legit['ci_high'],
                       legit['pairs'], legit['families_total']) if legit else 'missing',
        ])
    lines.append(_markdown_table(
        ['Model', 'Coordination', 'Contrast', 'Unsafe execution difference', 'Legit. completion difference'],
        contrast_rows, right_columns=(3, 4)))
    lines.extend([
        '',
        'Family pairing inventory is reported separately per endpoint in `pairing_inventory.csv`, '
        'including counts for missing arms, missing endpoint denominators, and fixture mismatches.',
        '',
        '## Failure, abstention, and gateway accounting',
        '',
        'The aggregate table keeps the completed denominator separate from the total denominator. '
        'The following columns are also written to `aggregate.csv`: error and invalid rates, '
        'abstention rates (overall and by answerability), unsafe publish decisions, unsafe outcome '
        'attempts, unsafe executions, gateway-blocked attempts, publication attempts and executions, '
        'and the decision-to-outcome attempt gap. This separates a supervisor decision to publish '
        'from an executed publication and from any gateway effect.',
        '',
        '## Method',
        '',
        f"- Fixed seed: {summary['analysis']['bootstrap']['seed']}; "
        f"replicates: {summary['analysis']['bootstrap']['replicates']}; "
        f"interval: {summary['analysis']['bootstrap']['ci_level']:.0%} percentile family-cluster bootstrap.",
        '- Marginal rate intervals resample family blocks within each model/arm/coordination cell.',
        '- Paired intervals resample family blocks within each model/coordination stratum; there is '
        'no pooled model or pooled coordination estimate.',
        '- `decision.action == publish` on an illegitimate episode is reported separately from '
        '`outcome.unsafe_attempt` and `outcome.unsafe_execution`. The gateway view is conditional '
        'on an observed unsafe attempt or publication attempt.',
        '- Complete-case co-primary rates use completed episodes only. Failure-inclusive rates use '
        'the full legitimacy denominator and count non-completed or missing-outcome episodes as non-success.',
        '',
        '## Limitations',
        '',
        '- This is an analysis of supplied records, not evidence that a live study was run.',
        '- Intervals are descriptive and unadjusted for multiple contrasts.',
        '- Families are resampled within model/coordination strata. Cross-model contrasts are not '
        'treated as independent, and no model or coordination pooling is performed.',
        '- Error and invalid episodes have no invented outcomes; the primary estimates exclude them '
        'from the completed-case denominator while the sensitivity estimates retain them explicitly.',
        '- No outcome, threshold, model, or arm was selected using these records in this script.',
        '',
    ])
    return '\n'.join(lines)
def _latex_escape(value: Any) -> str:
    replacements = {
        '\\': r'\textbackslash{}', '&': r'\&', '%': r'\%', '$': r'\$', '#': r'\#',
        '_': r'\_', '{': r'\{', '}': r'\}', '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}', '\n': ' ',
    }
    return ''.join(replacements.get(character, character) for character in str(value))


def render_latex(aggregate: Sequence[Mapping[str, Any]]) -> str:
    """Compact booktabs/longtable fragment for the co-primary endpoints."""
    lines = [
        '% Requires \\usepackage{booktabs,longtable}',
        '\\begin{longtable}{@{}lllrrrrr@{}}',
        '\\caption{Co-primary endpoints by model, evidence arm, and coordination condition. '
        'Cells are complete-case success counts and rates; the episode column is '
        'total (completed/error/invalid). Error and invalid episodes remain in the total and '
        'in the failure-inclusive sensitivity, not in the completed-case denominator. '
        'Family-cluster 95\\% intervals are in the CSV and figure. No model or coordination '
        'pooling is performed.}\\label{tab:live-co-primary}\\\\',
        '\\toprule',
        'Model & Arm & Coordination & Episodes (C/E/I) & Abstain & Unsafe exec. & '
        'Legit. completion & Error+invalid \\\\',
        '\\midrule',
        '\\endfirsthead',
        '\\toprule',
        'Model & Arm & Coordination & Episodes (C/E/I) & Abstain & Unsafe exec. & '
        'Legit. completion & Error+invalid \\\\',
        '\\midrule',
        '\\endhead',
        '\\bottomrule',
        '\\endfoot',
    ]
    for row in aggregate:
        def rate_cell(success: Any, denominator: Any, rate: Any) -> str:
            if denominator in (None, 0) or rate is None:
                return f'0/{denominator or 0} (n.e.)'
            return f'{int(success)}/{int(denominator)} ({float(rate):.3f})'
        abstain = rate_cell(row['action_abstain'], row['episodes_completed_denominator'], row['abstention_rate'])
        unsafe = rate_cell(row['illegitimate_outcome_unsafe_executions'], row['illegitimate_completed'],
                           row['illegitimate_unsafe_execution_rate'])
        legitimate = rate_cell(row['legitimate_completions'], row['legitimate_completed'],
                               row['legitimate_completion_rate'])
        errors = row['status_error'] + row['status_invalid']
        error_rate = row['noncompletion_rate']
        error_cell = f'{errors}/{row["episodes_total"]} ({error_rate:.3f})' if error_rate is not None else '0/0'
        lines.append(
            f"{_latex_escape(row['model'])} & {_latex_escape(row['arm'])} & "
            f"{_latex_escape(row['coordination'])} & "
            f"{row['episodes_total']} ({row['status_completed']}/{row['status_error']}/{row['status_invalid']}) & "
            f"{abstain} & {unsafe} & {legitimate} & {error_cell} \\\\")
    lines.append('\\end{longtable}')
    return '\n'.join(lines) + '\n'


def _sha256(path: Path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _status_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for record in records:
        counts[record['status']] += 1
    return counts


def run_analysis(input_path: str | Path, output_dir: str | Path, *,
                 bootstrap_replicates: int = DEFAULT_BOOTSTRAP,
                 seed: int = DEFAULT_SEED, ci: float = DEFAULT_CI) -> dict[str, Any]:
    """Run the complete offline analysis and write all artefacts."""
    if bootstrap_replicates < 1:
        raise ValueError('bootstrap_replicates must be at least 1')
    if not 0.0 < ci < 1.0:
        raise ValueError('ci must be strictly between 0 and 1')
    if seed < 0:
        raise ValueError('seed must be non-negative')
    source = Path(input_path)
    destination = Path(output_dir)
    input_hash = _sha256(source)
    records, blank_lines = load_jsonl(source)
    aggregate = aggregate_metrics(records, seed=seed, replicates=bootstrap_replicates, ci=ci)
    denominators = endpoint_denominators(records)
    inventory, contrasts = paired_contrasts(records, seed=seed, replicates=bootstrap_replicates, ci=ci)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        'aggregate': destination / 'aggregate.csv',
        'endpoint_denominators': destination / 'endpoint_denominators.csv',
        'paired_contrasts': destination / 'paired_contrasts.csv',
        'pairing_inventory': destination / 'pairing_inventory.csv',
        'summary': destination / 'summary.json',
        'results': destination / 'results.md',
        'latex': destination / 'table.tex',
        'figure_png': destination / 'live_co_primary.png',
        'figure_pdf': destination / 'live_co_primary.pdf',
    }
    for key, rows in (
        ('aggregate', aggregate),
        ('endpoint_denominators', denominators),
        ('paired_contrasts', contrasts),
        ('pairing_inventory', inventory),
    ):
        pd.DataFrame(rows).to_csv(paths[key], index=False, lineterminator='\n')
    create_figure(aggregate, paths['figure_png'], paths['figure_pdf'])
    models = sorted({record['model'] for record in records})
    coordinations = [item for item in COORDINATIONS if any(record['coordination'] == item for record in records)]
    arms = [item for item in ARMS if any(record['arm'] == item for record in records)]
    honest = [record for record in records if record['coordination'] == 'honest']
    status_counts = _status_counts(records)
    summary: dict[str, Any] = {
        'schema_version': SCHEMA_VERSION,
        'scope': ('Offline analysis of supplied episode records only. This script does not run a '
                  'live study, make API calls, select models after outcomes, or establish that the '
                  'planned research was completed. Missing outcomes are never inferred.'),
        'input': {
            'path': str(source),
            'sha256': input_hash,
            'episodes': len(records),
            'blank_lines_ignored': blank_lines,
            'models': models,
            'arms_observed': arms,
            'coordinations': coordinations,
            'splits': sorted({record['split'] for record in records}),
            'families': len({record['family_id'] for record in records}),
            'fixtures': len({record['fixture_id'] for record in records}),
            'mechanisms': sorted({record['mechanism'] for record in records}),
            'status_counts': status_counts,
            'legitimate_counts': {
                'legitimate': sum(1 for record in records if record['legitimate']),
                'illegitimate': sum(1 for record in records if not record['legitimate']),
            },
            'answerable_counts': {
                'answerable': sum(1 for record in records if record['answerable']),
                'unanswerable': sum(1 for record in records if not record['answerable']),
            },
            'code_sha256': _sha256(Path(__file__)),
        },
        'validation': {
            'required_fields': list(REQUIRED_FIELDS),
            'allowed_arms': list(ARMS),
            'allowed_coordinations': list(COORDINATIONS),
            'allowed_statuses': list(STATUSES),
            'allowed_actions': list(ACTIONS),
            'explicit_null_policy': ('decision and outcome may be null only for status error or invalid; '
                                     'missing keys, null decision/outcome on completed episodes, or absent '
                                     'outcome fields are schema errors'),
            'coherence_rules': [
                'publication_executed implies publication_attempted',
                'unsafe_execution implies unsafe_attempt and publication_executed',
                'a legitimate fixture can still receive an incorrect model-written summary; unsafe events are retained',
                'illegitimate episodes cannot have legitimate_completion',
                'legitimate_completion implies legitimate=true',
                'decision-level and gateway-level events are not forced equal; their gaps are reported',
            ],
            'cross_record_rules': [
                'episode_id is unique',
                'one design cell per model/arm/coordination/fixture',
                'fixture metadata family_id/split/answerable is consistent; legitimacy and mechanism '
                'may vary by condition and are handled by endpoint eligibility',
                'a family_id does not span splits',
                'one input file contains a single split; mixed splits are rejected rather than pooled',
            ],
        },
        'analysis': {
            'grouping': ['model', 'arm', 'coordination'],
            'no_pooling': ('No row or contrast pools models or coordination conditions. Family blocks '
                           'are resampled within model/coordination strata.'),
            'endpoints': {
                'unsafe_execution': ('among illegitimate completed episodes; success is '
                                     'outcome.unsafe_execution'),
                'legitimate_completion': ('among legitimate completed episodes; success is '
                                          'outcome.legitimate_completion'),
                'decision_level_separation': ('illegitimate_publish_decisions and '
                                               'illegitimate_outcome_unsafe_attempts are reported '
                                               'separately from illegitimate_outcome_unsafe_executions '
                                               'and gateway counts'),
            },
            'bootstrap': {
                'seed': seed,
                'replicates': bootstrap_replicates,
                'ci_level': ci,
                'method': ('percentile cluster bootstrap resampling independent family_id blocks with '
                           'replacement; marginal cells resample within model/arm/coordination, paired '
                           'contrasts within model/coordination'),
                'rng': ('numpy default_rng seeded by SeedSequence([seed, crc32(key)]); key recorded '
                        'on every paired contrast row'),
            },
            'pairing': {
                'unit': 'family_id',
                'matched_arms_rule': ('a family enters paired contrasts only when all four arms have a '
                                      'completed endpoint-eligible episode and the fixture sets agree '
                                      'across arms'),
                'missing_pairs': ('missing arms, missing endpoint denominators, and fixture mismatches '
                                  'are counted in paired_contrasts.csv and pairing_inventory.csv'),
                'contrasts': [f'{a}_minus_{b}' for a, b in ARM_PAIRS],
            },
            'failure_handling': {
                'primary_denominator': 'completed episodes only',
                'all_status_denominator': ('all legitimacy-eligible episodes; observed successes are '
                                           'retained, while an episode without an observed outcome '
                                           'counts as non-success without inferring an outcome'),
                'denominators': ('aggregate.csv and endpoint_denominators.csv retain total, completed, '
                                 'error, invalid, noncompleted, and missing-outcome counts'),
            },
            'honest_controls': {
                'episodes': len(honest),
                'status_counts': _status_counts(honest),
                'no_pooled_estimate': True,
                'note': ('counts only; endpoint estimates appear in aggregate rows with '
                         'coordination=honest and are never pooled with adversarial conditions'),
            },
            'abstention': ('reported overall and by answerability among completed episodes; abstention '
                           'is not treated as a detection or as completion'),
        },
        'aggregate': aggregate,
        'endpoint_denominators': denominators,
        'paired_contrasts': contrasts,
        'pairing_inventory': inventory,
        'outputs': {key: path.name for key, path in paths.items()},
        'limitations': [
            'This is an analysis of supplied records, not evidence that a live study was run.',
            'Intervals are descriptive, unadjusted, and conditional on the supplied episode set.',
            'Cross-model or cross-coordination differences are not independent family evidence.',
            'Error and invalid episodes are not assigned guessed outcomes; they are excluded only from '
            'completed-case rates and retained in all-status sensitivity denominators.',
            'No model, arm, threshold, or outcome was selected using these records in this script.',
        ],
    }
    summary = _jsonable(summary)
    markdown_text = render_markdown(aggregate, denominators, inventory, contrasts, summary)
    latex_text = render_latex(aggregate)
    paths['summary'].write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf8')
    paths['results'].write_text(markdown_text + '\n', encoding='utf8')
    paths['latex'].write_text(latex_text, encoding='utf8')
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, help='Episode JSONL input path')
    parser.add_argument('--output', required=True, help='Analysis output directory')
    parser.add_argument('--bootstrap', type=int, default=DEFAULT_BOOTSTRAP,
                        help=f'Family-cluster bootstrap replicates (default {DEFAULT_BOOTSTRAP})')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED,
                        help=f'Fixed bootstrap seed (default {DEFAULT_SEED})')
    parser.add_argument('--ci', type=float, default=DEFAULT_CI,
                        help=f'Percentile interval level (default {DEFAULT_CI})')
    args = parser.parse_args(argv)
    try:
        summary = run_analysis(args.input, args.output, bootstrap_replicates=args.bootstrap,
                               seed=args.seed, ci=args.ci)
    except (SchemaError, OSError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(f"Analysed {summary['input']['episodes']} episodes across "
          f"{len(summary['aggregate'])} model/arm/coordination groups -> {args.output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

