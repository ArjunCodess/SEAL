import json

from scripts import run_wording_v2 as study
from src import live_fixtures as fx, live_workers as workers
from src.seal_followups import digest


def test_honest_single_preserves_world_and_gate():
    for index in range(40):
        original = fx.make_fixture("test", index, "honest")
        single = study.fixture_for(index, "honest_single")
        assert len(single["worker_messages"]) == 1
        assert workers.fixture_truth_unchanged(original, single)
        result = fx.execute_decision(single, "provenance", {"action": "publish", "summary": single["true_summary"]})
        assert result["legitimate_completion"]


def test_no_reference_in_actual_worker_prompt():
    fixture = study.fixture_for(0, "attack_single")
    payload = study.worker_payload(fixture, fixture["worker_messages"][0], [], 0)
    user = json.loads(payload["input"])
    assert "wording_reference" not in user
    assert "public_totals" in user
    assert "wording_reference" not in payload["instructions"]


def test_all_conditions_have_unique_text_bound_supervisor_ids():
    corpus = []
    messages = 0
    for index in range(40):
        for condition in study.CONDITIONS:
            fixture = study.fixture_for(index, condition)
            messages += len(fixture["worker_messages"])
            corpus.append({"index": index, "condition": condition, "valid": True,
                           "messages": fixture["worker_messages"]})
    entries, denominators = study.supervisor_entries(corpus)
    assert messages == 320
    assert len(entries) == len(denominators) == 1280
    assert len({digest(row["identity"]) for row in entries}) == 1280
    honest_single = next(e for e in entries if e["condition"] == "honest_single")
    honest_many = next(e for e in entries if e["condition"] == "honest_coordinated")
    assert honest_single["fixture"]["fixture_id"] == honest_many["fixture"]["fixture_id"]
    assert digest(honest_single["identity"]) != digest(honest_many["identity"])


def test_missing_generation_keeps_all_denominator_cells():
    corpus = [{"index": 0, "condition": "attack_single", "valid": False, "messages": []}]
    entries, denominators = study.supervisor_entries(corpus)
    assert not entries
    assert len(denominators) == 8
    assert all(not d["generation_available"] for d in denominators)


def test_copying_is_recorded_without_rejection(tmp_path):
    from src.seal_followups import CallStore
    from src import live_study as ls, seal_collection as sc
    fixture = study.fixture_for(0, "attack_single")
    baseline = fixture["worker_messages"][0]

    class CopyingClient:
        def create(self, **payload):
            return {"_http_status": 200, "status": "completed",
                    "output_text": json.dumps(baseline),
                    "usage": {"input_tokens": 10, "output_tokens": 5}}

    b = ls.validate_budget_config(sc.read_json(sc.ROOT / "configs/seal/stage4_budget.json"))
    store = CallStore(tmp_path / "workers", {"name": "copy-audit-test"}, b, client=CopyingClient())
    condition = study.generate_condition(store, 0, "attack_single")
    assert condition["valid"]
    result = json.loads(next((store.directory / "results").glob("*.json")).read_text())
    assert result["exact_copy"]
    assert store.ledger["request_count"] == 1
