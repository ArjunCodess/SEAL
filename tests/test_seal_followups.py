import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src import live_study as ls, seal_collection as sc
from src.seal_followups import CallStore, require_frozen, supervisor_result


class Client:
    def __init__(self, text='{"action":"publish","summary":{},"reason":"test"}'):
        self.count = 0
        self.text = text

    def create(self, **payload):
        self.count += 1
        return {"status": "completed", "_http_status": 200, "output_text": self.text,
                "usage": {"input_tokens": 10, "output_tokens": 5}}


def budget():
    return ls.validate_budget_config(sc.read_json(sc.ROOT / "configs/seal/stage4_budget.json"))


def payload():
    return ls.build_request_payload("gemma-4-31b-it", {}, max_output_tokens=100)


def test_pending_is_not_replayed(tmp_path):
    client = Client()
    store = CallStore(tmp_path / "run", {"name": "test"}, budget(), client=client)
    assert store.protocol_hash == sc.sha256_file(store.directory / 'protocol.json')
    store.call({"id": 1}, payload())
    store.ledger["episodes"]["uncertain"] = {"status": "pending"}
    store.ledger_path.write_text(json.dumps(store.ledger))
    with pytest.raises(ValueError, match="pending"):
        CallStore(tmp_path / "run", {"name": "test"}, budget(), client=client)
    assert client.count == 1


def test_concurrent_reservations_and_resume(tmp_path):
    client = Client()
    store = CallStore(tmp_path / "run", {"name": "test"}, budget(), client=client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda i: store.call({"id": i}, payload()), range(8)))
    assert store.ledger["request_count"] == client.count == 8
    assert abs(store.ledger["reserved_total"]) < 1e-8
    for row in rows:
        store.save_result(row)
    store.call({"id": 0}, payload())
    assert client.count == 8
    store.freeze(8)
    require_frozen(store.directory)
    with pytest.raises(ValueError, match="frozen"):
        CallStore(store.directory, {"name": "test"}, budget(), client=client)


def test_changed_protocol_refused(tmp_path):
    CallStore(tmp_path / "run", {"name": "one"}, budget(), client=Client())
    with pytest.raises(ValueError, match="drift"):
        CallStore(tmp_path / "run", {"name": "two"}, budget(), client=Client())


def test_tampered_frozen_parent_refused(tmp_path):
    store = CallStore(tmp_path / "run", {"name": "test"}, budget(), client=Client())
    store.call({"id": 0}, payload())
    with pytest.raises(ValueError, match="Missing parsed"):
        store.freeze(1)
    row = json.loads(next((store.directory / "calls").glob("*.json")).read_text())
    store.save_result(row)
    store.freeze(1)
    (store.directory / "protocol.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        require_frozen(store.directory)


def test_invalid_decision_retained_as_invalid(tmp_path):
    store = CallStore(tmp_path / "run", {"name": "test"}, budget(), client=Client("not JSON"))
    fixture = sc.live_fixtures.make_fixture("test", 0, "single")
    row = supervisor_result(store, {"identity": {"id": 0}, "payload": payload(),
                                   "fixture": fixture, "arm": "provenance", "condition": "single"})
    assert row["status"] == "invalid"
    assert row["execution"] is None
    assert store.ledger["request_count"] == 1


def test_request_budget_reserved_before_send(tmp_path):
    b = budget()
    b["max_requests"] = 1
    client = Client()
    store = CallStore(tmp_path / "run", {"name": "test"}, b, client=client)
    store.call({"id": 0}, payload())
    with pytest.raises(ValueError, match="cap"):
        store.call({"id": 1}, payload())
    assert client.count == 1


def test_live_retry_refuses_unfrozen_parent(tmp_path):
    from scripts.run_gate_retry import prepare
    with pytest.raises(ValueError, match="not frozen"):
        prepare(tmp_path)


def test_missing_credential_refused_before_scheduling(tmp_path, monkeypatch):
    monkeypatch.delenv('ADAPTION_API_KEY', raising=False)
    with pytest.raises(ValueError, match='credential'):
        CallStore(tmp_path / 'run', {'name': 'test'}, budget())
    assert not (tmp_path / 'run').exists()


def test_retry_eligibility_and_original_observation_are_preserved(tmp_path):
    from scripts.run_gate_retry import prepare
    parent = tmp_path / "blind"
    sc.collect(parent, budget_config_path=sc.ROOT / "configs/seal/stage4_budget.json",
               fixture_indices=[0], client=Client())
    sc.freeze_study(parent)
    protocol, entries = prepare(parent)
    assert len(entries) == 4
    assert len({e["identity"]["parent_episode_id"] for e in entries}) == 4
    assert protocol["maximum_retries_per_parent"] == 1
    original = {r["request_sha256"]: r for r in sc.load_responses_log(parent / "responses.jsonl")}
    for entry in entries:
        previous = json.loads(original[entry["parent"]["request_sha256"]]["request_input"])
        retried = json.loads(entry["payload"]["input"])
        assert retried.pop("retry_context")["block_reason"] == entry["parent"]["block_reason"]
        assert retried == json.loads(previous["input"])
        assert retried["observation"]["provenance"]["trusted_summary"] is None
