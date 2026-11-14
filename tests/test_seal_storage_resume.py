import pytest

from scripts import resume_paced_storage as resume
from src import seal_collection as sc, seal_followups as follow


def test_only_local_writes_are_retried(monkeypatch, tmp_path):
    attempts = []
    def write(path, value):
        attempts.append((path, value))
        if len(attempts) < 3:
            raise PermissionError('Locked')
    monkeypatch.setattr(resume, 'ORIGINAL_WRITE', write)
    monkeypatch.setattr(resume.time, 'sleep', lambda _: None)
    resume.write_with_replace_retry(tmp_path / 'ledger.json', {'reserved': 1})
    assert len(attempts) == 3
    assert all(row == attempts[0] for row in attempts)


def setup_failure(tmp_path):
    root = tmp_path / 'study'
    target = root / 'supervisors'
    follow.write_json(target / 'protocol.json', {'name': 'test'})
    schedule = [{'identity': {'id': 1}, 'request_sha256': 'request-hash'}]
    follow.write_json(target / 'schedule.json', schedule)
    eid = follow.digest({'protocol': sc.sha256_file(target / 'protocol.json'), **schedule[0]})
    follow.write_json(target / 'ledger.json', {'episodes': {eid: {'status': 'pending', 'reservation': .1}},
        'reserved_total': .1, 'spent_total': 0, 'request_count': 1})
    trace = tmp_path / 'trace.txt'
    trace.write_text('write_json(self.ledger_path, self.ledger)\nPermissionError')
    return root, target, eid, trace


def test_proven_unsent_reservation_can_be_released_with_checkpoint(tmp_path):
    root, target, eid, trace = setup_failure(tmp_path)
    original = (target / 'ledger.json').read_bytes()
    result = resume.reconcile_unsent(root, trace, confirmed_dead_pid=123)
    assert result['requests_replayed'] == 0
    assert result['known_unsent_reservations_released'] == [eid]
    assert sc.read_json(target / 'ledger.json')['request_count'] == 0
    assert (root / 'storage_checkpoint/ledger.json').read_bytes() == original


def test_persisted_pending_call_is_never_replayed(tmp_path):
    root, target, eid, trace = setup_failure(tmp_path)
    follow.write_json(target / 'calls' / f'{eid}.json', {'status': 'pending'})
    with pytest.raises(ValueError, match='must never be replayed'):
        resume.reconcile_unsent(root, trace, confirmed_dead_pid=123)


def test_frozen_study_is_rejected_before_credentials_or_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, 'ROOT', tmp_path)
    monkeypatch.delenv('ADAPTION_API_KEY', raising=False)
    follow.write_json(tmp_path / 'data/study_wording_v3/checksums.json', {})
    monkeypatch.setattr(resume.follow, 'require_frozen', lambda _: 'verified')
    with pytest.raises(ValueError, match='study is frozen'):
        resume.run()
    assert not (tmp_path / 'data/study_wording_v3/storage_resume_driver.py').exists()
