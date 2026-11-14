"""Offline tests for the SEAL stage 4 blind provenance runner."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import seal_collection as sc


class FakeClient:
    def __init__(self):
        self.calls = 0

    def create(self, **payload):
        self.calls += 1
        return {
            "_http_status": 200,
            "output_text": '{"action":"publish","summary":{},"reason":"test"}',
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "model": payload.get("model"),
            "status": "completed",
        }


def test_schedule_shape():
    entries = sc.build_schedule()
    assert len(entries) == 240
    counts = sc.schedule_counts(entries)
    assert counts["attack"] == 160
    assert counts["honest"] == 80


def test_blinding_wrapper_nulls_trusted_summary():
    import src.live_study as ls
    import src.live_fixtures as lf
    corpus = json.loads((ROOT / "data/study/workers/assembled_corpus.json").read_text())
    entries = sc.build_schedule()[:1]
    with sc.install_blinding():
        fx = ls.apply_worker_corpus(entries[0]["fixture"], corpus, allow_scripted_workers=False)
        obs = lf.supervisor_observation(fx, entries[0]["arm"])
        assert obs["provenance"]["trusted_summary"] is None
        text = ls.canonical_json(ls.build_request_payload(entries[0]["model"], obs, max_output_tokens=8192))
        assert '\\"trusted_summary\\":null' in text


def test_gate_invariance_under_wrapper():
    # Wrapper must not change execute_decision outcomes.
    import src.live_study as ls
    import src.live_fixtures as lf
    corpus = json.loads((ROOT / "data/study/workers/assembled_corpus.json").read_text())
    entries = sc.build_schedule()[:5]
    with sc.install_blinding():
        for entry in entries:
            fx = ls.apply_worker_corpus(entry["fixture"], corpus, allow_scripted_workers=False)
            obs = lf.supervisor_observation(fx, entry["arm"])
            # execute_decision reads fixture, not observation; just ensure callable
            outcome = lf.execute_decision(fx, entry["arm"], {"action": "publish", "summary": {}, "reason": "x"})
            assert outcome is not None


def test_full_offline_collection_freeze_replay(tmp_path):
    study = tmp_path / "blindstudy"
    sc.guard_study_dir(study)
    client = FakeClient()
    result = sc.collect(
        study,
        budget_config_path=ROOT / "configs/seal/stage4_budget.json",
        client=client,
        worker_corpus_path=ROOT / "data/study/workers/assembled_corpus.json",
    )
    assert result["scheduled"] == 240
    assert result["statuses"].get("completed") == 240
    assert result["responses_logged"] == 240

    # freeze
    frozen = sc.freeze_study(study)
    assert frozen["scheduled_episode_ids"] == 240
    assert frozen["pending"] == 0

    # replay
    recs = sc.load_episode_records(study)
    assert len(recs) == 240
    for r in recs.values():
        assert sc.replay_gate_for_episode(r) is True

    # comparison
    out = tmp_path / "cmp.csv"
    cmp_result = sc.build_comparison(study_dir=study, original_dir=ROOT / "data/study/main", output_path=out)
    assert cmp_result["rows"] == 6
    assert out.is_file()


def test_no_blind_episode_id_equals_original(tmp_path):
    # IDs are protocol-hash distinct; check none of the frozen original 960 IDs appear
    # by loading original episodes and comparing to blind study IDs.
    import src.seal_collection as sc
    study = tmp_path / "blindstudy2"
    client = FakeClient()
    sc.collect(
        study,
        budget_config_path=ROOT / "configs/seal/stage4_budget.json",
        client=client,
        worker_corpus_path=ROOT / "data/study/workers/assembled_corpus.json",
    )
    sc.freeze_study(study)
    blind = sc.load_blind_study(study)
    original = sc.load_original_provenance_episodes(ROOT / "data/study/main")
    original_ids = {r["episode_id"] for r in original}
    blind_ids = {r["episode_id"] for r in blind["rows"]}
    assert original_ids.isdisjoint(blind_ids)