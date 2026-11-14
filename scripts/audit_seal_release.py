"""Reconcile frozen follow-up calls, no-replay identities, and credit envelopes."""
from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import live_study as ls, seal_collection as sc, seal_followups as follow
from scripts.seal_wording_cohorts import frozen_cohorts

# Release-time pins.  These are cross-checks on verified frozen ledgers, not
# replacement observations: every value below is read from the root ledger and
# then required to match the frozen release inventory.
RELEASE_TOTAL_CREDITS = 6.39271922
WORDING_ENVELOPE_CREDITS = 100.0
DEVELOPMENT_ENVELOPE_CREDITS = 50.0
RELEASE_STAGE_ORDER = (
    ('stage4_blind_provenance', None),
    ('stage5_gate_retry', None),
    ('stage6_wording_v2_diagnostic', 'diagnostic'),
    ('stage6_wording_v3_primary', 'primary'),
)
RELEASE_STAGE_SPECS = {
    'stage4_blind_provenance': {
        'directory': ROOT / 'data/study_blind_provenance',
        'request_count': 240,
        'spent_total': 1.29058114,
        'parts': (),
        'envelope_group': 'stage4_blind_provenance',
    },
    'stage5_gate_retry': {
        'directory': ROOT / 'data/study_gate_retry',
        'request_count': 114,
        'spent_total': 0.54697127,
        'parts': (),
        'envelope_group': 'stage5_gate_retry',
    },
    'stage6_wording_v2_diagnostic': {
        'directory': ROOT / 'data/study_wording_v2',
        'request_count': 504,
        'spent_total': 1.43374151,
        'parts': (('workers', 320), ('supervisors', 184)),
        'envelope_group': 'stage6_wording_shared',
    },
    'stage6_wording_v3_primary': {
        'directory': ROOT / 'data/study_wording_v3',
        'request_count': 952,
        'spent_total': 3.12142530,
        'parts': (('workers', 320), ('supervisors', 632)),
        'envelope_group': 'stage6_wording_shared',
    },
}


def audit_calls(directory):
    """Check the durable request record against its identity and settled ledger."""
    checksum = follow.require_frozen(directory)
    protocol_hash = sc.sha256_file(directory / 'protocol.json')
    budget = sc.read_json(directory / 'protocol.json')['budget']
    ledger = sc.read_json(directory / 'ledger.json')
    calls = {p.stem: sc.read_json(p) for p in (directory / 'calls').glob('*.json')}
    results = {p.stem: sc.read_json(p) for p in (directory / 'results').glob('*.json')}
    if set(calls) != set(results) or set(calls) != set(ledger['episodes']):
        raise ValueError('Call, result, and ledger IDs do not reconcile')
    charged = 0.
    for eid, call in calls.items():
        request_hash = follow.digest(call['request'])
        expected_id = follow.digest({'protocol': protocol_hash, 'identity': call['identity'],
                                     'request_sha256': request_hash})
        if (call['request_sha256'] != request_hash or expected_id != eid or
                call['protocol_sha256'] != protocol_hash):
            raise ValueError('Request or identity drift')
        if call['raw_response_sha256'] != follow.digest(call['raw_response']):
            raise ValueError('Response drift')
        if call['raw_response'] is not None and ls.extract_output_text(call['raw_response']) != call['output_text']:
            raise ValueError('Saved output text differs from response')
        if call['status'] not in ('completed', 'error'):
            raise ValueError('Nonterminal transport observation')
        expected_charge = call['reservation']
        if call['status'] == 'completed' and isinstance(call['raw_response'].get('usage'), dict):
            usage = call['raw_response']['usage']
            expected_charge = ls.estimate_reservation(budget, int(usage.get('input_tokens') or 0),
                int(usage.get('output_tokens') or 0), model=call['request']['model'])
        if not math.isclose(expected_charge, call['charged_cost'], abs_tol=1e-10):
            raise ValueError('Conservative charge does not reconcile')
        if ledger['episodes'][eid]['charged_cost'] != call['charged_cost']:
            raise ValueError('Ledger charge differs from call')
        for field in ('request_sha256', 'raw_response_sha256', 'protocol_sha256'):
            if results[eid][field] != call[field]:
                raise ValueError('Parsed result lost observation linkage')
        result = results[eid]
        if 'decision' in result and call['status'] == 'completed':
            try:
                decision = ls.parse_supervisor_decision(call['output_text'])
            except ValueError:
                if result['status'] != 'invalid' or result['decision'] is not None:
                    raise ValueError('Invalid raw supervisor output was treated as a decision')
            else:
                if result['status'] != 'completed' or decision != result['decision']:
                    raise ValueError('Saved decision differs from parsed model output')
        charged += call['charged_cost']
    if (ledger['request_count'] != len(calls) or abs(ledger['reserved_total']) > 1e-8 or
            not math.isclose(charged, ledger['spent_total'], abs_tol=1e-8)):
        raise ValueError('Terminal ledger does not reconcile')
    if charged > budget['spend_cap']:
        raise ValueError('Stage credit envelope exceeded')
    return {'checksums_sha256': checksum, 'calls': len(calls), 'charged_credits': charged,
            'result_statuses': dict(Counter(r['status'] for r in results.values())),
            'unknown_delivery': sum(c.get('delivery_status') == 'unknown_no_replay' for c in calls.values())}


def _ledger_metadata(directory, *, label, expected_requests, allow_aggregate=False):
    ledger = sc.read_json(Path(directory) / 'ledger.json')
    episodes = ledger.get('episodes')
    if episodes is None and allow_aggregate:
        # Wording roots are aggregate ledgers; their phase subledgers carry the
        # per-request inventory and are reconciled separately below.
        pending = 0
        charged = None
    elif not isinstance(episodes, dict):
        raise ValueError(f'{label} ledger has no episode inventory')
    else:
        pending = sum(meta.get('status') == 'pending' for meta in episodes.values())
        charged = sum(float(meta['charged_cost']) for meta in episodes.values())
    if pending:
        raise ValueError(f'{label} ledger contains {pending} pending request(s)')
    reserved = float(ledger.get('reserved_total', 0.0))
    if abs(reserved) > 1e-8:
        raise ValueError(f'{label} ledger retains a nonzero reservation')
    request_count = int(ledger.get('request_count', -1))
    if isinstance(episodes, dict) and request_count != len(episodes):
        raise ValueError(f'{label} ledger request count differs from its episode inventory')
    if request_count != expected_requests:
        raise ValueError(f'{label} ledger has {request_count} requests; expected {expected_requests}')
    spent = float(ledger.get('spent_total', 0.0))
    if charged is not None and not math.isclose(charged, spent, abs_tol=1e-8):
        raise ValueError(f'{label} ledger spends do not sum to its terminal total')
    return {'request_count': request_count, 'reserved_total': reserved, 'spent_total': spent,
            'pending_requests': 0}


def verified_release_ledger(spec, *, directory=None):
    """Read one frozen real root ledger and require the release inventory.

    Only root ledgers under ``data/`` are accepted, so a pytest temporary
    collection path can never become release budget evidence.  Wording roots
    must equal their workers+supervisors subledgers, which makes counting the
    root once both complete and non-duplicative.
    """
    directory = Path(spec['directory'] if directory is None else directory)
    data_root = (ROOT / 'data').resolve()
    if not directory.resolve().is_relative_to(data_root):
        raise ValueError(f'Release budget status refuses a non-data root: {directory.resolve()}')
    checksum = follow.require_frozen(directory)
    ledger = _ledger_metadata(directory, label=spec.get('label', directory.name),
                              expected_requests=int(spec['request_count']),
                              allow_aggregate=bool(spec.get('parts')))
    if not math.isclose(ledger['spent_total'], float(spec['spent_total']), abs_tol=1e-8):
        raise ValueError(f"{directory.name} spent total {ledger['spent_total']} differs from the frozen release total")
    subledgers = {}
    for part, expected_requests in spec.get('parts', ()):
        part_dir = directory / part
        part_ledger = _ledger_metadata(part_dir, label=f'{directory.name}/{part}',
                                       expected_requests=expected_requests)
        subledgers[part] = part_ledger
    if subledgers:
        if ledger['request_count'] != sum(row['request_count'] for row in subledgers.values()):
            raise ValueError(f'{directory.name} root request count differs from its phase subledgers')
        if not math.isclose(ledger['spent_total'],
                            sum(row['spent_total'] for row in subledgers.values()), abs_tol=1e-8):
            raise ValueError(f'{directory.name} root spend differs from its phase subledgers')
    return {
        'study_dir': directory.relative_to(ROOT).as_posix(),
        'checksums_sha256': checksum,
        'ledger': ledger,
        'subledgers': subledgers,
        'pending_requests': 0,
        'envelope_group': spec['envelope_group'],
    }


def _envelope_group(envelope, stages, note):
    committed = sum(stage['committed_credits'] for stage in stages)
    return {
        'envelope_credits': envelope,
        'committed_credits': round(committed, 9),
        'envelope_remaining_credits': round(envelope - committed, 9),
        'stages': [stage['stage'] for stage in stages],
        'note': note,
    }


def build_budget_status(*, cohorts=None, write_path=None):
    """Build the release budget report from verified frozen root ledgers.

    The four roots are counted exactly once: blind provenance, gate retry,
    diagnostic wording V2, and primary wording V3.  Their phase subledgers are
    reconciliation checks only, never additional stages.  No pending request is
    allowed in any accepted ledger.
    """
    cohorts = frozen_cohorts() if cohorts is None else cohorts
    if set(cohorts) != {'diagnostic', 'primary'}:
        raise ValueError('Release budget status requires both frozen wording cohorts')
    expected_dirs = {
        'diagnostic': Path(RELEASE_STAGE_SPECS['stage6_wording_v2_diagnostic']['directory']),
        'primary': Path(RELEASE_STAGE_SPECS['stage6_wording_v3_primary']['directory']),
    }
    for cohort, expected in expected_dirs.items():
        if cohorts[cohort].resolve() != expected.resolve():
            raise ValueError(f'{cohort} wording cohort resolves outside the registered release path')

    stages = {}
    for key, cohort in RELEASE_STAGE_ORDER:
        spec = dict(RELEASE_STAGE_SPECS[key])
        spec['label'] = key
        directory = cohorts[cohort] if cohort else spec['directory']
        stage = verified_release_ledger(spec, directory=directory)
        stage['stage'] = key
        stage['committed_credits'] = round(stage['ledger']['spent_total'] + stage['ledger']['reserved_total'], 9)
        stage['envelope_credits'] = {
            'stage4_blind_provenance': sc.STAGE_ENVELOPES_CREDITS['stage4_blind_provenance'],
            'stage5_gate_retry': sc.STAGE_ENVELOPES_CREDITS['stage5_gate_retry'],
        }.get(key)
        stage['envelope_remaining_credits'] = (
            round(stage['envelope_credits'] - stage['committed_credits'], 9)
            if stage['envelope_credits'] is not None else None)
        stages[key] = stage

    total = sum(stage['committed_credits'] for stage in stages.values())
    if not math.isclose(total, RELEASE_TOTAL_CREDITS, abs_tol=1e-8):
        raise ValueError(f'Release credit total {total} differs from {RELEASE_TOTAL_CREDITS}')
    wording_stages = [stages['stage6_wording_v2_diagnostic'], stages['stage6_wording_v3_primary']]
    wording_total = sum(stage['committed_credits'] for stage in wording_stages)
    if wording_total > WORDING_ENVELOPE_CREDITS:
        raise ValueError('Shared wording ceiling exceeded')
    groups = {
        'stage4_blind_provenance': _envelope_group(
            sc.STAGE_ENVELOPES_CREDITS['stage4_blind_provenance'],
            [stages['stage4_blind_provenance']], 'Dedicated blind-provenance envelope.'),
        'stage5_gate_retry': _envelope_group(
            sc.STAGE_ENVELOPES_CREDITS['stage5_gate_retry'],
            [stages['stage5_gate_retry']], 'Dedicated one-retry envelope.'),
        'stage6_wording_shared': _envelope_group(
            WORDING_ENVELOPE_CREDITS, wording_stages,
            'One shared 100-credit wording ceiling for diagnostic V2 and primary V3; '
            'it is not 150 credits, and the two cohorts are never summed as separate envelopes.'),
        'development_and_collection_failures': {
            'envelope_credits': DEVELOPMENT_ENVELOPE_CREDITS,
            'committed_credits': 0.0,
            'envelope_remaining_credits': DEVELOPMENT_ENVELOPE_CREDITS,
            'stages': [],
            'note': 'Separate 50-credit development/collection-failure envelope; no root development '
                    'ledger is present or charged here.',
        },
    }
    status = {
        'schema': sc.BUDGET_STATUS_SCHEMA,
        'generated_at_unix': time.time(),
        'generated_by': 'scripts/audit_seal_release.py:build_budget_status',
        'source_basis': 'Verified frozen real root ledgers only; each root counted once',
        'authorized_total_credits': sc.AUTHORIZED_CEILING_CREDITS,
        'authorized_ceiling_basis': 'User-reported remaining ceiling, not API-verified account balance',
        'authorized_balance_api_verified': False,
        'historical_original_ledger_credits': sc.ORIGINAL_LEDGER_SPENT_CREDITS,
        'historical_note': ('the recorded study\'s 4.10737974 credits were already spent and are '
                            'reference only; they are NOT subtracted again from the remaining ceiling'),
        'historical_original_spend_subtracted_again': False,
        'remaining_ceiling_for_new_stages_credits': sc.AUTHORIZED_CEILING_CREDITS,
        'allocated_envelopes_credits': sc.ALLOCATED_ENVELOPES_TOTAL_CREDITS,
        'stage_envelopes_credits': {
            'stage4_blind_provenance': sc.STAGE_ENVELOPES_CREDITS['stage4_blind_provenance'],
            'stage5_gate_retry': sc.STAGE_ENVELOPES_CREDITS['stage5_gate_retry'],
            'stage6_wording_shared': WORDING_ENVELOPE_CREDITS,
            'development_and_collection_failures': DEVELOPMENT_ENVELOPE_CREDITS,
        },
        'unallocated_reserve_credits': sc.UNALLOCATED_RESERVE_CREDITS,
        'envelope_groups': groups,
        'stage_order': [key for key, _ in RELEASE_STAGE_ORDER],
        'stages': stages,
        'total_requests': sum(stage['ledger']['request_count'] for stage in stages.values()),
        'committed_all_stages_credits': round(total, 9),
        'extension_charges_total_credits': round(total, 9),
        'pending_requests': 0,
        'pending_reservations_credits': 0.0,
        'no_pending': True,
        'pending_check': ('All four frozen root ledgers and the wording phase subledgers '
                          'contain no pending episodes, and reserved_total is zero '
                          'within 1e-8.'),
        'double_counting_check': ('Wording root ledgers equal their workers+supervisors subledgers; '
                                  'diagnostic V2 and primary V3 each appear exactly once.'),
        'release_total_check': {
            'expected_credits': RELEASE_TOTAL_CREDITS,
            'observed_credits': round(total, 9),
            'matches': True,
        },
    }
    if write_path is not None:
        follow.write_json(Path(write_path), status)
    return status


def run():
    sc.verify_original_pins()
    sc.verify_source_contract()
    cohorts = frozen_cohorts()
    if 'primary' not in cohorts:
        raise ValueError('Primary wording evidence is not frozen')
    report = {'schema': 'seal-release-audit-v1', 'inference_calls': 0, 'studies': {}}
    from scripts.seal_worker_audit import original_worker_audit
    report['original_worker_copy_audit'] = original_worker_audit()
    blind = ROOT / 'data/study_blind_provenance'
    follow.require_frozen(blind)
    blind_manifest = sc.read_json(blind / 'freeze_manifest.json')
    blind_ledger = sc.read_json(blind / 'ledger.json')
    report['studies']['blind'] = {'checksums_sha256': sc.sha256_file(blind / 'checksums.json'),
                                'charged_credits': blind_ledger['spent_total'],
                                'freeze_manifest': blind_manifest}
    report['studies']['retry'] = audit_calls(ROOT / 'data/study_gate_retry')
    from scripts.run_gate_retry import prepare
    _, expected_retries = prepare(blind)
    expected = {entry['identity']['parent_episode_id']: follow.digest(entry['payload']) for entry in expected_retries}
    observed = {}
    for path in (ROOT / 'data/study_gate_retry/calls').glob('*.json'):
        record = sc.read_json(path)
        parent = record['identity']['parent_episode_id']
        if parent in observed:
            raise ValueError('More than one retry for a parent')
        observed[parent] = record['request_sha256']
    if observed != expected:
        raise ValueError('Retry calls differ from eligible parents or feedback-only requests')
    wording_total = 0.
    for name, root in cohorts.items():
        amendment = sc.read_json(root / 'workers/validation_amendment.json')
        if sc.sha256_file(root / 'workers/sources/scripts/resume_wording_validation.py') != amendment['validator_sha256']:
            raise ValueError('Worker validation amendment source snapshot differs')
        if name == 'primary' and (root / 'storage_amendment.json').exists():
            storage = sc.read_json(root / 'storage_amendment.json')
            if sc.sha256_file(root / 'storage_resume_driver.py') != storage['source_sha256']:
                raise ValueError('Executed storage amendment source snapshot differs')
        parts = {phase: audit_calls(root / phase) for phase in ('workers', 'supervisors')}
        total = sum(part['charged_credits'] for part in parts.values())
        if not math.isclose(total, sc.read_json(root / 'ledger.json')['spent_total'], abs_tol=1e-8):
            raise ValueError('Combined wording ledger differs from subledgers')
        for path in (root / 'workers/calls').glob('*.json'):
            user_input = json.loads(sc.read_json(path)['request']['input'])
            if 'wording_reference' in user_input:
                raise ValueError('Reference wording leaked into a new worker request')
        from scripts.run_wording_v2 import supervisor_entries
        entries, denominators = supervisor_entries(sc.read_json(root / 'workers/corpus.json'))
        if sc.read_json(root / 'supervisors/denominators.json') != denominators:
            raise ValueError('Planned wording denominator grid differs from frozen corpus')
        expected = {follow.digest(entry['identity']): follow.digest(entry['payload']) for entry in entries}
        observed = {}
        for path in (root / 'supervisors/calls').glob('*.json'):
            record = sc.read_json(path)
            identity = follow.digest(record['identity'])
            if identity in observed:
                raise ValueError('Repeated wording supervisor identity')
            observed[identity] = record['request_sha256']
        if observed != expected:
            raise ValueError('Supervisor calls differ from the frozen worker text schedule')
        report['studies'][f'wording_{name}'] = {'parts': parts, 'charged_credits': total}
        wording_total += total
    total = (blind_ledger['spent_total'] + report['studies']['retry']['charged_credits'] + wording_total)
    if wording_total > WORDING_ENVELOPE_CREDITS or total > sc.AUTHORIZED_CEILING_CREDITS:
        raise ValueError('Cross-study authorized credit envelope exceeded')
    budget_status = build_budget_status(cohorts=cohorts)
    if not math.isclose(total, budget_status['committed_all_stages_credits'], abs_tol=1e-8):
        raise ValueError('Release audit total differs from the generated budget status')
    report['budget_status'] = {
        'path': sc.BUDGET_STATUS_PATH.relative_to(ROOT).as_posix(),
        'schema': budget_status['schema'],
        'committed_all_stages_credits': budget_status['committed_all_stages_credits'],
        'stage_request_counts': {key: stage['ledger']['request_count']
                                 for key, stage in budget_status['stages'].items()},
        'no_pending': budget_status['no_pending'],
        'authorized_ceiling_basis': budget_status['authorized_ceiling_basis'],
        'wording_shared_envelope_credits': WORDING_ENVELOPE_CREDITS,
        'development_envelope_credits': DEVELOPMENT_ENVELOPE_CREDITS,
        'historical_original_spend_subtracted_again': False,
    }
    report.update(wording_combined_charged_credits=wording_total, extensions_charged_credits=total,
                  authorized_ceiling_credits=sc.AUTHORIZED_CEILING_CREDITS,
                  historical_original_spend_subtracted_again=False,
                  charges_basis='Usage charges when available; full reservation for errors or unknown delivery',
                  balance_basis='User-reported remaining ceiling, not API-verified account balance')
    follow.write_json(sc.BUDGET_STATUS_PATH, budget_status)
    follow.write_json(ROOT / 'build/seal/release_audit.json', report)
    return report


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
