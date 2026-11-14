import json
from types import SimpleNamespace

import pytest

from scripts import run_paced_wording as paced


def test_pacing_cools_down_future_requests_without_retries(monkeypatch):
    clock = [0.]
    sent = []
    responses = iter([{'_http_status': 429}, {'_http_status': 200}, {'_http_status': 200}])
    def create(**payload):
        sent.append(clock[0])
        return next(responses)
    monkeypatch.setattr(paced.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(paced.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    monkeypatch.setattr(paced.ls, 'HttpResponsesClient', lambda: SimpleNamespace(create=create))
    client = paced.PacedClient()
    for _ in range(3):
        client.create(model='fake')
    assert sent == [0., 65., 68.]
    assert client.sent == 3


def test_interruption_preserves_pending_bytes_and_recovers_saved_response(tmp_path):
    calls = tmp_path / 'calls'
    calls.mkdir()
    pending = {'status': 'pending', 'request': {'model': 'fake'}, 'reservation': .4}
    recovered = {'status': 'completed', 'raw_response': {'output': 'saved'}, 'charged_cost': .1}
    paced.follow.write_json(calls / 'a.json', pending)
    paced.follow.write_json(calls / 'b.json', recovered)
    original_bytes = (calls / 'a.json').read_bytes()
    paced.follow.write_json(tmp_path / '.run.lock', {'pid': 123})
    paced.follow.write_json(tmp_path / 'ledger.json', {'episodes': {
        'a': {'status': 'pending', 'reservation': .4},
        'b': {'status': 'pending', 'reservation': .6}},
        'spent_total': 1., 'reserved_total': 1., 'request_count': 2})
    result = paced.settle_interrupted(tmp_path, confirmed_dead_pid=123)
    assert result['uncertain_calls'] == ['a']
    assert result['recovered_terminal_calls'] == ['b']
    assert (tmp_path / 'interruption_checkpoint/a.json').read_bytes() == original_bytes
    assert json.loads((calls / 'b.json').read_text()) == recovered
    assert json.loads((calls / 'a.json').read_text())['delivery_status'] == 'unknown_no_replay'
    ledger = json.loads((tmp_path / 'ledger.json').read_text())
    assert ledger['spent_total'] == pytest.approx(1.5)
    assert ledger['reserved_total'] == 0
    assert not (tmp_path / '.run.lock').exists()
    assert paced.settle_interrupted(tmp_path, 123) == result


def test_interruption_refuses_wrong_process(tmp_path):
    paced.follow.write_json(tmp_path / '.run.lock', {'pid': 123})
    with pytest.raises(ValueError, match='does not match'):
        paced.settle_interrupted(tmp_path, 456)
