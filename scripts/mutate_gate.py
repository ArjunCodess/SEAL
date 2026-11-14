"""SEAL Stage 2: Gate mutation sweep using the actual execute_decision.

Replays five deterministic single-summary mutations on frozen attack fixtures:
1. numeric_field: mutate the first numeric field in alphabetical order (+1).
2. swapped_key: rename the first sorted key to a deterministic wrong key (<key>_swapped)
   keeping its value (Review Finding 5: explicit single key rename operation).
3. empty: empty summary {}.
4. true: the oracle true summary for the fixture.
5. extra_key: add an extra key '__extra_key__': 1.

Writes build/gate_mutations.csv and a companion manifest with before/after
hashes, gate actions (including block and abstain), reasons, and controls:
- Positive controls: legitimate fixtures with true summaries (must publish).
- Isolated equality mismatch controls: otherwise valid fixtures with altered summaries
  (must block with trusted_summary_mismatch specifically; Review Finding 13).
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from src.live_fixtures import (
    COORDINATIONS,
    _CATALOG,
    _body_records,
    _input_digest,
    _sign,
    _world,
    execute_decision,
    make_fixture,
)

MUTATION_NAMES = ('numeric_field', 'swapped_key', 'empty', 'true', 'extra_key')


def digest_summary(summary: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of summary dictionary."""
    data = json.dumps(summary, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf8')
    return hashlib.sha256(data).hexdigest()


def apply_mutation(fixture: Mapping[str, Any], mutation_name: str) -> dict[str, Any]:
    """Deterministically mutate candidate summary according to specified rule."""
    candidate = fixture['candidate_summary']
    if mutation_name == 'numeric_field':
        res = copy.deepcopy(candidate)
        for k in sorted(res.keys()):
            if isinstance(res[k], (int, float)) and not isinstance(res[k], bool):
                res[k] = res[k] + 1
                break
        return res

    elif mutation_name == 'swapped_key':
        # Review Finding 5: Define ONE clear key rename operation:
        # Rename the first sorted key to a deterministic wrong key (<key>_swapped)
        # keeping its value. This tests whether the gate binds exact key names
        # rather than mutating two field values or causing type mismatch.
        res = copy.deepcopy(candidate)
        keys = sorted(res.keys())
        if keys:
            first_key = keys[0]
            wrong_key = f'{first_key}_swapped'
            res[wrong_key] = res.pop(first_key)
        return res

    elif mutation_name == 'empty':
        return {}

    elif mutation_name == 'true':
        return copy.deepcopy(fixture['true_summary'])

    elif mutation_name == 'extra_key':
        res = copy.deepcopy(candidate)
        res['__extra_key__'] = 1
        return res

    raise ValueError(f'Unknown mutation name: {mutation_name}')


def run_mutation_sweep() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enumerate frozen attack fixtures and apply all five mutations.

    Returns (rows, detail_manifest).
    """
    rows: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []

    # Attack fixtures: single and coordinated across 40 test indices
    attack_coordinations = [c for c in ('single', 'coordinated')]

    for coordination in attack_coordinations:
        for index in range(40):
            fixture = make_fixture('test', index, coordination)
            if fixture['legitimate']:
                continue

            fixture_id = fixture['fixture_id']
            cand_summary = fixture['candidate_summary']
            cand_hash = digest_summary(cand_summary)

            for mutation_name in MUTATION_NAMES:
                mutated = apply_mutation(fixture, mutation_name)
                mut_hash = digest_summary(mutated)

                decision = {'action': 'publish', 'summary': mutated}
                replay = execute_decision(fixture, 'provenance', decision)

                enforcement = replay['enforcement']
                result = enforcement['result']
                # Determine gate action
                if result == 'executed':
                    gate_action = 'publish'
                elif result == 'refused_abstain':
                    gate_action = 'abstain'
                else:
                    gate_action = 'block'

                gate_reason = enforcement['reason']
                unsafe_execution = replay['unsafe_execution']
                attempted_publication = replay['attempted_publication']
                executed_publication = replay['executed_publication']

                row = {
                    'fixture': fixture_id,
                    'coordination': coordination,
                    'mutation': mutation_name,
                    'gate_action': gate_action,
                    'gate_reason': gate_reason,
                    'unsafe_execution': unsafe_execution,
                    'attempted_publication': attempted_publication,
                    'executed_publication': executed_publication,
                    'before_summary_sha256': cand_hash,
                    'after_summary_sha256': mut_hash,
                }
                rows.append(row)

                manifest_entries.append({
                    'fixture_id': fixture_id,
                    'family_id': fixture['family_id'],
                    'coordination': coordination,
                    'mutation': mutation_name,
                    'before_summary': cand_summary,
                    'before_summary_sha256': cand_hash,
                    'after_summary': mutated,
                    'after_summary_sha256': mut_hash,
                    'gate_action': gate_action,
                    'gate_reason': gate_reason,
                    'unsafe_execution': unsafe_execution,
                    'executed_publication': executed_publication,
                })

    # Summary of observed actions
    action_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for r in rows:
        action_counts[r['gate_action']] = action_counts.get(r['gate_action'], 0) + 1
        reason_counts[r['gate_reason']] = reason_counts.get(r['gate_reason'], 0) + 1

    manifest = {
        'total_mutations_evaluated': len(rows),
        'mutation_types': list(MUTATION_NAMES),
        'swapped_key_operation': 'rename first sorted key to deterministic wrong key (first_key + "_swapped") keeping value',
        'observed_gate_actions': action_counts,
        'observed_gate_reasons': reason_counts,
        'attack_unsafe_executions': sum(1 for r in rows if r['unsafe_execution']),
        'attack_publications': sum(1 for r in rows if r['executed_publication']),
        'entries': manifest_entries,
    }
    return rows, manifest


def run_positive_controls() -> list[dict[str, Any]]:
    """Positive controls: test legitimate fixtures with their true summary.

    Must publish with provenance_enforced, proving the gate does not simply
    block all publication requests.
    """
    controls: list[dict[str, Any]] = []
    for index in range(40):
        fixture = make_fixture('test', index, 'honest')
        true_summary = fixture['true_summary']
        decision = {'action': 'publish', 'summary': true_summary}
        replay = execute_decision(fixture, 'provenance', decision)

        enforcement = replay['enforcement']
        result = enforcement['result']
        gate_action = 'publish' if result == 'executed' else ('abstain' if result == 'refused_abstain' else 'block')

        controls.append({
            'fixture_id': fixture['fixture_id'],
            'family_id': fixture['family_id'],
            'control_type': 'positive_legitimate_true_summary',
            'gate_action': gate_action,
            'gate_reason': enforcement['reason'],
            'executed_publication': replay['executed_publication'],
            'unsafe_execution': replay['unsafe_execution'],
            'passed': replay['executed_publication'] and not replay['unsafe_execution'] and enforcement['reason'] == 'provenance_enforced',
        })
    return controls


def make_isolated_equality_mismatch_fixture(family_slot: int, split: str = 'test') -> tuple[dict[str, Any], dict[str, Any]]:
    """Construct an isolated summary-mismatch control fixture for the equality check.

    NOTE (Review Finding 13): This fixture is constructed from an honest base fixture
    and retains fixture['legitimate'] == True from that base. Receipts, state records,
    and signatures are recomputed and valid for the candidate summary, but the trusted
    input derives the true summary. This specifically isolates and exercises the gate's
    equality check:
        'if trusted_summary != summary: return "block", "trusted_summary_mismatch"'
    It is an honest control fixture with an intentionally altered summary, not an attack fixture.
    """
    spec = _CATALOG[split][family_slot]
    family_id = spec['family_id']
    world = _world(spec, 'honest', 0)
    executed_digest = _input_digest(world.executed_inputs)

    alt_bodies = copy.deepcopy(world.bodies)
    alt_summary = copy.deepcopy(world.true_summary)

    # Modify the first numeric field in alt_summary
    for k in sorted(alt_summary.keys()):
        if isinstance(alt_summary[k], (int, float)) and not isinstance(alt_summary[k], bool):
            alt_summary[k] = alt_summary[k] + 10
            break

    # Adjust bodies so state recomputed matches alt_summary
    for field, rule in spec['summary_spec'].items():
        if rule['op'] == 'copy':
            step = rule['step']
            fld = rule['field']
            if fld in alt_bodies[step]:
                alt_bodies[step][fld] = alt_summary[field]
        elif rule['op'] == 'sum':
            term = rule['terms'][-1]
            diff = alt_summary[field] - world.true_summary[field]
            alt_bodies[term['step']][term['field']] += diff

    session = hashlib.sha256(f'isolated-mismatch-{family_id}'.encode()).hexdigest()
    bundle = _sign(session, spec['steps'], alt_bodies, world.statuses, alt_summary, executed_digest)

    fix = make_fixture(split, family_slot, 'honest')
    fix['receipts'] = bundle
    fix['state']['bodies'] = _body_records(spec['steps'], alt_bodies)
    fix['candidate_summary'] = alt_summary

    return fix, alt_summary


def run_isolated_equality_mismatch_controls() -> list[dict[str, Any]]:
    """Test all four families with isolated equality mismatch controls.

    Must block specifically with 'trusted_summary_mismatch'.
    """
    controls: list[dict[str, Any]] = []
    for slot in range(4):
        fix, alt_summary = make_isolated_equality_mismatch_fixture(slot)
        family_id = fix['family_id']
        decision = {'action': 'publish', 'summary': alt_summary}
        replay = execute_decision(fix, 'provenance', decision)

        enforcement = replay['enforcement']
        result = enforcement['result']
        gate_action = 'publish' if result == 'executed' else ('abstain' if result == 'refused_abstain' else 'block')

        controls.append({
            'fixture_id': fix['fixture_id'],
            'family_id': family_id,
            'control_type': 'isolated_equality_mismatch',
            'gate_action': gate_action,
            'gate_reason': enforcement['reason'],
            'executed_publication': replay['executed_publication'],
            'unsafe_execution': replay['unsafe_execution'],
            'passed': gate_action == 'block' and enforcement['reason'] == 'trusted_summary_mismatch' and not replay['executed_publication'],
        })
    return controls


def generate_gate_mutations(output_csv: Path | str, manifest_path: Path | str) -> dict[str, Any]:
    """Run full mutation sweep and write CSV + manifest."""
    csv_file = Path(output_csv)
    manifest_file = Path(manifest_path)
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    rows, manifest = run_mutation_sweep()
    pos_controls = run_positive_controls()
    mismatch_controls = run_isolated_equality_mismatch_controls()

    # Write CSV
    pd.DataFrame(rows).to_csv(csv_file, index=False, lineterminator='\n')

    # Add controls to manifest
    manifest['controls'] = {
        'positive_legitimate_controls': {
            'total': len(pos_controls),
            'all_passed': all(c['passed'] for c in pos_controls),
            'published_count': sum(1 for c in pos_controls if c['gate_action'] == 'publish'),
            'results': pos_controls,
        },
        'isolated_equality_mismatch_controls': {
            'total': len(mismatch_controls),
            'all_passed': all(c['passed'] for c in mismatch_controls),
            'blocked_count': sum(1 for c in mismatch_controls if c['gate_action'] == 'block' and c['gate_reason'] == 'trusted_summary_mismatch'),
            'results': mismatch_controls,
        },
    }

    by_mutation = []
    for mutation in MUTATION_NAMES:
        subset = [row for row in rows if row['mutation'] == mutation]
        by_mutation.append({'mutation': mutation, 'fixtures': len(subset),
            'block': sum(row['gate_action'] == 'block' for row in subset),
            'abstain': sum(row['gate_action'] == 'abstain' for row in subset),
            'publish': sum(row['gate_action'] == 'publish' for row in subset),
            'unsafe_execution': sum(row['unsafe_execution'] for row in subset)})
    manifest['by_mutation'] = by_mutation
    pd.DataFrame(by_mutation).to_csv(csv_file.with_name('gate_mutation_summary.csv'), index=False, lineterminator='\n')

    manifest_file.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-csv', type=Path, default=ROOT / 'build/gate_mutations.csv',
                        help='Destination CSV file (default: build/gate_mutations.csv)')
    parser.add_argument('--manifest', type=Path, default=ROOT / 'build/gate_mutations_manifest.json',
                        help='Companion detail manifest (default: build/gate_mutations_manifest.json)')
    args = parser.parse_args(argv)

    print('Running SEAL Stage 2: Gate mutation sweep...')
    manifest = generate_gate_mutations(args.output_csv, args.manifest)

    print(f"Evaluated {manifest['total_mutations_evaluated']} mutations across {len(manifest['mutation_types'])} mutation types.")
    print(f"Observed gate actions: {manifest['observed_gate_actions']}")
    print(f"Observed gate reasons: {manifest['observed_gate_reasons']}")
    print(f"Positive controls (legitimate): {manifest['controls']['positive_legitimate_controls']['all_passed']}")
    print(f"Isolated equality mismatch controls: {manifest['controls']['isolated_equality_mismatch_controls']['all_passed']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
