"""Resume unsent wording cells after a pre-transport Windows replace failure.

Original collector sources and request identities stay fixed. Only local file
writes are retried; a persisted pending call is never resent.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import seal_collection as sc, seal_followups as follow
from scripts import run_paced_wording as paced, run_wording_v2 as study


def write_with_replace_retry(path, value):
    for attempt in range(8):
        try:
            ORIGINAL_WRITE(path, value)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.025 * 2 ** attempt, 1.))


ORIGINAL_WRITE = follow.write_json


def reconcile_unsent(root, trace_path, *, confirmed_dead_pid):
    target = root / 'supervisors'
    amendment_path = root / 'storage_amendment.json'
    if amendment_path.exists():
        return sc.read_json(amendment_path)
    trace = trace_path.read_text(encoding='utf8')
    if 'write_json(self.ledger_path, self.ledger)' not in trace or 'PermissionError' not in trace:
        raise ValueError('Failure evidence does not identify pre-transport ledger write failure')
    if (target / '.run.lock').exists():
        raise ValueError('Collector lock still exists; do not resume a live collector')
    ledger_path = target / 'ledger.json'
    ledger = sc.read_json(ledger_path)
    pending = {eid: row for eid, row in ledger['episodes'].items() if row['status'] == 'pending'}
    if len(pending) != 1:
        raise ValueError('Expected exactly the single pre-transport reservation in the failure evidence')
    protocol_hash = sc.sha256_file(target / 'protocol.json')
    scheduled = {follow.digest({'protocol': protocol_hash, 'identity': row['identity'],
                               'request_sha256': row['request_sha256']}) for row in sc.read_json(target / 'schedule.json')}
    for eid in pending:
        if (target / 'calls' / f'{eid}.json').exists() or eid not in scheduled:
            raise ValueError('A durable or unknown pending call must never be replayed')
    checkpoint = root / 'storage_checkpoint'
    checkpoint.mkdir()
    (checkpoint / 'ledger.json').write_bytes(ledger_path.read_bytes())
    (checkpoint / 'failure_trace.txt').write_bytes(trace_path.read_bytes())
    reservation = sum(row['reservation'] for row in pending.values())
    if abs(ledger['reserved_total'] - reservation) > 1e-8:
        raise ValueError('Unsettled reservations do not match the pre-send failure')
    for eid in pending:
        del ledger['episodes'][eid]
    ledger['request_count'] -= len(pending)
    ledger['reserved_total'] = 0.
    write_with_replace_retry(ledger_path, ledger)
    amendment = {'schema': 'seal-local-storage-amendment-v1', 'confirmed_absent_pid': confirmed_dead_pid,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'failure': 'PermissionError at ledger reservation write, before call file and HTTP transport',
        'known_unsent_reservations_released': sorted(pending), 'released_credits': reservation,
        'requests_replayed': 0, 'reasoning': 'The captured traceback precedes durable call creation and HTTP dispatch; the sole pending identity has no call file.',
        'source_sha256': sc.sha256_file(Path(__file__)), 'write_attempts_maximum': 8,
        'unchanged': ['request identities', 'prompts', 'model settings', 'pacing', 'generation policy', 'credit ceilings']}
    write_with_replace_retry(amendment_path, amendment)
    return amendment


def run():
    root = ROOT / 'data/study_wording_v3'
    if (root / 'checksums.json').exists():
        follow.require_frozen(root)
        raise ValueError('Primary wording study is frozen; use reproduce_all.py for an offline rebuild')
    if not os.environ.get('ADAPTION_API_KEY'):
        raise ValueError('Missing inference credential')
    diagnostic = ROOT / 'data/study_wording_v2'
    follow.require_frozen(diagnostic)
    follow.require_frozen(root / 'workers')
    reconcile_unsent(root, ROOT / 'build/seal/paced_collection.stderr.log', confirmed_dead_pid=36032)
    (root / 'storage_resume_driver.py').write_bytes(Path(__file__).read_bytes())
    follow.write_json = write_with_replace_retry
    diagnostic_cost = sc.read_json(diagnostic / 'ledger.json')['spent_total']
    previous = paced.install_pacing(primary=True, diagnostic_cost=diagnostic_cost)
    try:
        result = study.supervise(SimpleNamespace(study_dir=root,
            budget_config=ROOT / 'configs/seal/stage4_budget.json'))
    finally:
        paced.restore(previous)
        follow.write_json = ORIGINAL_WRITE
    follow.require_frozen(root)
    follow.write_json(ROOT / 'build/seal/studies_complete.json', {
        'blind': follow.require_frozen(ROOT / 'data/study_blind_provenance'),
        'retry': follow.require_frozen(ROOT / 'data/study_gate_retry'),
        'wording_diagnostic': follow.require_frozen(diagnostic),
        'wording_primary': follow.require_frozen(root),
        'primary_selection_sha256': sc.sha256_file(ROOT / 'data/wording_cohort_selection.json')})
    print(json.dumps({'phase': 'all_studies_frozen', 'result': result}), flush=True)


if __name__ == '__main__':
    run()
