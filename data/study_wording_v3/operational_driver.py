"""Complete the diagnostic wording cohort and collect one paced replication.

This operational amendment never retries a saved request. The primary cohort
is preselected because of HTTP 429 failures, before inspecting harm outcomes.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import live_study as ls, seal_collection as sc, seal_followups as follow
from scripts import run_wording_v2 as study, resume_wording_validation as validation

SPACING_SECONDS = 3.
COOLDOWN_SECONDS = 65.


class PacedClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_send = 0.
        self.sent = 0

    def create(self, **payload):
        with self.lock:
            delay = self.next_send - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_send = time.monotonic() + SPACING_SECONDS
            self.sent += 1
        response = ls.HttpResponsesClient().create(**payload)
        if response.get('_http_status') == 429:
            with self.lock:
                self.next_send = max(self.next_send, time.monotonic() + COOLDOWN_SECONDS)
            print(json.dumps({'event': 'rate_limit_cooldown', 'seconds': COOLDOWN_SECONDS,
                              'failed_request_retried': False}), flush=True)
        if self.sent % 20 == 0:
            print(json.dumps({'event': 'paced_requests_sent', 'count': self.sent}), flush=True)
        return response


def settle_interrupted(directory, confirmed_dead_pid):
    """Record unknown delivery conservatively, retaining every original byte."""
    directory = Path(directory)
    event_path = directory / 'interruption_amendment.json'
    if event_path.exists():
        return sc.read_json(event_path)
    lock_path = directory / '.run.lock'
    if sc.read_json(lock_path)['pid'] != confirmed_dead_pid:
        raise ValueError('Stale lock does not match the process confirmed absent by the supervisor')
    ledger_path = directory / 'ledger.json'
    ledger = sc.read_json(ledger_path)
    pending = {eid: row for eid, row in ledger['episodes'].items() if row['status'] == 'pending'}
    total = sum(row['reservation'] for row in pending.values())
    if not math.isclose(ledger['reserved_total'], total, abs_tol=1e-8):
        raise ValueError('Pending reservations do not reconcile')
    checkpoint = directory / 'interruption_checkpoint'
    checkpoint.mkdir()
    (checkpoint / 'ledger.json').write_bytes(ledger_path.read_bytes())
    unknown, recovered, settled_charge = [], [], 0.
    for eid, meta in pending.items():
        path = directory / 'calls' / f'{eid}.json'
        original = sc.read_json(path)
        (checkpoint / f'{eid}.json').write_bytes(path.read_bytes())
        if original['status'] in ('completed', 'error'):
            # A crash may occur after the response file is durable but before
            # ledger settlement. Recover that observation rather than erase it.
            charge = original['charged_cost']
            ledger['episodes'][eid] = {'status': original['status'], 'charged_cost': charge}
            settled_charge += charge
            recovered.append(eid)
            continue
        if original['status'] != 'pending':
            raise ValueError('Unrecognized interrupted call state')
        record = {**original, 'status': 'error', 'raw_response': None,
                  'raw_response_sha256': follow.digest(None), 'output_text': '',
                  'error': 'interrupted_delivery_unknown', 'charged_cost': meta['reservation'],
                  'delivery_status': 'unknown_no_replay',
                  'settled_at_utc': datetime.now(timezone.utc).isoformat()}
        follow.write_json(path, record)
        ledger['episodes'][eid] = {'status': 'error', 'charged_cost': meta['reservation'],
                                   'delivery_status': 'unknown_no_replay'}
        settled_charge += meta['reservation']
        unknown.append(eid)
    ledger['spent_total'] += settled_charge
    ledger['reserved_total'] = 0.
    follow.write_json(ledger_path, ledger)
    event = {'schema': 'seal-interrupted-delivery-amendment-v1',
             'confirmed_absent_process_id': confirmed_dead_pid, 'uncertain_calls': sorted(unknown),
             'recovered_terminal_calls': sorted(recovered),
             'reservation_charged_credits': settled_charge, 'requests_replayed': 0,
             'original_pending_bytes': 'interruption_checkpoint',
             'source_sha256': sc.sha256_file(Path(__file__)),
             'settled_at_utc': datetime.now(timezone.utc).isoformat()}
    follow.write_json(event_path, event)
    lock_path.unlink()
    return event


def install_pacing(primary, diagnostic_cost=0.):
    client = PacedClient()
    base_store = follow.CallStore
    base_protocol = study.protocol
    base_budget = study.make_budget

    class PacedStore(base_store):
        def __init__(self, directory, protocol, budget, *, client=None):
            super().__init__(directory, protocol, budget, client=pacer)

    pacer = client
    def protocol(phase, parents):
        result = base_protocol(phase, parents)
        if primary:
            result.update(name=f'SEAL-WORDING-V3-{phase}',
                operational_driver_sha256=sc.sha256_file(Path(__file__)),
                diagnostic_cohort_checksums_sha256=follow.require_frozen(ROOT / 'data/study_wording_v2'),
                request_spacing_seconds=SPACING_SECONDS, rate_limit_cooldown_seconds=COOLDOWN_SECONDS,
                rate_limit_retry_policy='No replay; cooldown applies to future unsent requests only',
                primary_selection='Operational HTTP 429 failure, not model safety outcomes')
        return result

    def budget(path, maximum, cap):
        result = base_budget(path, maximum, cap)
        if primary:
            result['spend_cap'] = max(0., result['spend_cap'] - diagnostic_cost)
        return result

    follow.CallStore = PacedStore
    study.protocol = protocol
    study.make_budget = budget
    return base_store, base_protocol, base_budget


def restore(previous):
    follow.CallStore, study.protocol, study.make_budget = previous


def run():
    if not os.environ.get('ADAPTION_API_KEY'):
        raise ValueError('Missing inference credential')
    diagnostic = ROOT / 'data/study_wording_v2'
    primary = ROOT / 'data/study_wording_v3'
    config = ROOT / 'configs/seal/stage4_budget.json'
    if not (diagnostic / 'checksums.json').exists():
        settle_interrupted(diagnostic / 'supervisors', confirmed_dead_pid=18112)
        follow.write_json(diagnostic / 'operational_pacing_amendment.json', {
            'source_sha256': sc.sha256_file(Path(__file__)), 'request_spacing_seconds': SPACING_SECONDS,
            'cooldown_seconds': COOLDOWN_SECONDS, 'saved_requests_replayed': False,
            'reason': 'HTTP 429 rate limits and an interrupted collector; prompts and model settings unchanged'})
        previous = install_pacing(primary=False)
        try:
            print(json.dumps({'phase': 'complete_diagnostic', 'result': study.supervise(
                SimpleNamespace(study_dir=diagnostic, budget_config=config))}), flush=True)
        finally:
            restore(previous)
    diagnostic_hash = follow.require_frozen(diagnostic)
    diagnostic_cost = sc.read_json(diagnostic / 'ledger.json')['spent_total']
    selection = {'schema': 'seal-primary-wording-cohort-v1', 'primary': 'data/study_wording_v3',
                 'diagnostic': 'data/study_wording_v2', 'diagnostic_checksums_sha256': diagnostic_hash,
                 'basis': 'HTTP 429 operational collection failure; no safety outcome selection',
                 'one_new_replication': True, 'wording_total_envelope_credits': 100.,
                 'diagnostic_charges_included': diagnostic_cost,
                 'request_spacing_seconds': SPACING_SECONDS, 'driver_sha256': sc.sha256_file(Path(__file__))}
    selection_path = ROOT / 'data/wording_cohort_selection.json'
    if selection_path.exists() and sc.read_json(selection_path) != selection:
        raise ValueError('Primary cohort selection changed')
    follow.write_json(selection_path, selection)
    previous = install_pacing(primary=True, diagnostic_cost=diagnostic_cost)
    try:
        args = SimpleNamespace(study_dir=primary, budget_config=config)
        primary.mkdir(parents=True, exist_ok=True)
        source = primary / 'operational_driver.py'
        if not source.exists():
            source.write_bytes(Path(__file__).read_bytes())
        if not (primary / 'workers/checksums.json').exists():
            print(json.dumps({'phase': 'primary_workers', 'result': validation.generate(args)}), flush=True)
        if not (primary / 'checksums.json').exists():
            print(json.dumps({'phase': 'primary_supervisors', 'result': study.supervise(args)}), flush=True)
    finally:
        restore(previous)
    follow.require_frozen(primary)
    follow.write_json(ROOT / 'build/seal/studies_complete.json', {
        'blind': follow.require_frozen(ROOT / 'data/study_blind_provenance'),
        'retry': follow.require_frozen(ROOT / 'data/study_gate_retry'),
        'wording_diagnostic': diagnostic_hash, 'wording_primary': follow.require_frozen(primary),
        'primary_selection_sha256': sc.sha256_file(selection_path)})
    print(json.dumps({'phase': 'all_studies_frozen'}), flush=True)


if __name__ == '__main__':
    run()
