"""Unit tests for live worker message generation (mocked API, no credentials)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import live_workers
from src.live_study import ResumeError, apply_worker_corpus

SAMPLE_BUDGET = {
    "unit_name": "credits",
    "input_price_per_million": 1.0,
    "output_price_per_million": 2.0,
    "verified_balance": 100.0,
    "inference_eligible": True,
    "no_overage": True,
    "max_input_chars": 50_000,
    "max_output_tokens": 512,
    "max_requests": 1000,
    "spend_cap": 50.0,
    "worker_spend_cap": 5.0,
    "reserve_fraction": 0.2,
}


def _good_content(role: str = "roster_clerk") -> str:
    return (
        f"{role} finished the north-wing handoff. active 4, confirmed 4, wing north.\n"
        f"{live_workers.CLAIM_STATUS_SUFFIX}"
    )


def _response(text: str, *, input_tokens: int = 12, output_tokens: int = 8) -> dict:
    return {
        "status": "completed",
        "_http_status": 200,
        "output_text": text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


class MockResponsesClient:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls = 0

    def create(self, **payload):
        self.calls += 1
        text = self.texts.pop(0)
        return _response(text)


class TestParsing(unittest.TestCase):
    def test_parse_plain_json_content(self):
        message = live_workers.parse_worker_message_text(
            json.dumps({"role": "bin_scanner", "content": _good_content("bin_scanner")}),
            expected_role="bin_scanner",
        )
        self.assertEqual(message["role"], "bin_scanner")

    def test_parse_output_text_field(self):
        raw = _response(json.dumps({"role": "bin_scanner", "content": _good_content("bin_scanner")}))
        message = live_workers.parse_worker_response(
            raw, "", expected_role="bin_scanner"
        )
        self.assertIn("claim_status:", message["content"])

    def test_reject_malformed_json(self):
        with self.assertRaises(ValueError):
            live_workers.parse_worker_message_text("not json", expected_role="x")

    def test_reject_forbidden_ground_truth_keys(self):
        payload = json.dumps(
            {
                "role": "bin_scanner",
                "content": _good_content("bin_scanner"),
                "legitimate": True,
            }
        )
        with self.assertRaises(ValueError):
            live_workers.parse_worker_message_text(payload, expected_role="bin_scanner")

    def test_reject_wrong_role(self):
        payload = json.dumps({"role": "other", "content": _good_content("other")})
        with self.assertRaises(ValueError):
            live_workers.parse_worker_message_text(payload, expected_role="bin_scanner")


class TestValidation(unittest.TestCase):
    def test_boundary_content_length(self):
        fixture = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0][
            "fixture"
        ]
        too_long = "x" * (live_workers.MAX_WORKER_CONTENT_CHARS + 1) + f"\n{live_workers.CLAIM_STATUS_SUFFIX}"
        with self.assertRaises(ValueError):
            live_workers.validate_accepted_worker_message(
                {"role": fixture["worker_messages"][0]["role"], "content": too_long},
                fixture=fixture,
            )

    def test_label_leak_rejected(self):
        fixture = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0][
            "fixture"
        ]
        role = fixture["worker_messages"][0]["role"]
        leaked = f"Mechanism false_completion is active.\n{live_workers.CLAIM_STATUS_SUFFIX}"
        with self.assertRaises(ValueError):
            live_workers.validate_accepted_worker_message({"role": role, "content": leaked}, fixture=fixture)

    def test_prompt_has_no_hidden_labels(self):
        entry = live_workers.schedule_fixtures("test", indices=[0], coordinations=["coordinated"])[0]
        payload = live_workers.build_worker_user_input(
            role=entry["fixture"]["worker_messages"][1]["role"],
            fixture=entry["fixture"],
            coordination=entry["coordination"],
            message_index=1,
            prior_messages=[entry["fixture"]["worker_messages"][0]],
            scripted_baseline=entry["fixture"]["worker_messages"][1],
        )
        for token in live_workers.FORBIDDEN_MESSAGE_SUBSTRINGS:
            if token in ("candidate_summary", "true_summary", "family_id", "fixture_id"):
                self.assertNotIn(token, payload)


class TestGeneration(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.budget_path = self.base / "budget.json"
        self.budget_path.write_text(json.dumps(SAMPLE_BUDGET), encoding="utf8")
        self.paths = {
            "base": self.base,
            "corpus": self.base / "worker_corpus.json",
            "audit": self.base / "worker_generation_audit.jsonl",
            "ledger": self.base / "worker_ledger.json",
            "manifest": self.base / "generation_manifest.json",
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_role_assignments_preserved(self):
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["coordinated"])[0]
        role_a = entry["fixture"]["worker_messages"][0]["role"]
        role_b = entry["fixture"]["worker_messages"][1]["role"]
        client = MockResponsesClient(
            [
                json.dumps({"role": role_a, "content": _good_content(role_a)}),
                json.dumps({"role": role_b, "content": _good_content(role_b)}),
            ]
        )
        ledger = live_workers.load_worker_ledger(self.paths["ledger"])
        protocol = live_workers.worker_protocol_sha256()
        first = live_workers.generate_one_worker_message(
            entry,
            message_index=0,
            accepted_prior=[],
            budget=SAMPLE_BUDGET,
            model="gemma-4-31b-it",
            protocol_hash=protocol,
            client=client,
            ledger=ledger,
            paths=self.paths,
            dry_run=False,
            max_output_tokens=512,
        )
        ledger = live_workers.load_worker_ledger(self.paths["ledger"])
        second = live_workers.generate_one_worker_message(
            entry,
            message_index=1,
            accepted_prior=[first["accepted_message"]],
            budget=SAMPLE_BUDGET,
            model="gemma-4-31b-it",
            protocol_hash=protocol,
            client=client,
            ledger=ledger,
            paths=self.paths,
            dry_run=False,
            max_output_tokens=512,
        )
        self.assertEqual(first["accepted_message"]["role"], role_a)
        self.assertEqual(second["accepted_message"]["role"], role_b)
        self.assertEqual(client.calls, 2)

    def test_coordinated_prompt_includes_prior_claim(self):
        entry = live_workers.schedule_fixtures("test", indices=[1], coordinations=["coordinated"])[0]
        prior = [{"role": "a", "content": "prior claim line\nclaim_status: complete"}]
        payload = live_workers.build_worker_user_input(
            role=entry["fixture"]["worker_messages"][1]["role"],
            fixture=entry["fixture"],
            coordination="coordinated",
            message_index=1,
            prior_messages=prior,
            scripted_baseline=entry["fixture"]["worker_messages"][1],
        )
        self.assertIn("prior claim line", payload)

    def test_rejected_output_recorded_without_resampling(self):
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0]
        bad = json.dumps({"role": entry["fixture"]["worker_messages"][0]["role"], "content": "too short"})
        client = MockResponsesClient([bad])
        ledger = live_workers.load_worker_ledger(self.paths["ledger"])
        result = live_workers.generate_one_worker_message(
            entry,
            message_index=0,
            accepted_prior=[],
            budget=SAMPLE_BUDGET,
            model="gemma-4-31b-it",
            protocol_hash=live_workers.worker_protocol_sha256(),
            client=client,
            ledger=ledger,
            paths=self.paths,
            dry_run=False,
            max_output_tokens=512,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(client.calls, 1)
        audit_lines = self.paths["audit"].read_text(encoding="utf8").strip().splitlines()
        self.assertEqual(len(audit_lines), 1)

    def test_budget_refusal(self):
        tight_budget = {**SAMPLE_BUDGET, "worker_spend_cap": 0.0}
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0]
        role = entry["fixture"]["worker_messages"][0]["role"]
        client = MockResponsesClient([json.dumps({"role": role, "content": _good_content(role)})])
        ledger = live_workers.load_worker_ledger(self.paths["ledger"])
        result = live_workers.generate_one_worker_message(
            entry,
            message_index=0,
            accepted_prior=[],
            budget=tight_budget,
            model="gemma-4-31b-it",
            protocol_hash=live_workers.worker_protocol_sha256(),
            client=client,
            ledger=ledger,
            paths=self.paths,
            dry_run=False,
            max_output_tokens=512,
        )
        self.assertEqual(result["status"], "refused_budget")
        self.assertEqual(client.calls, 0)

    def test_resume_skips_attempted_call(self):
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0]
        role = entry["fixture"]["worker_messages"][0]["role"]
        call_id = live_workers.generation_call_id(
            live_workers.worker_protocol_sha256(),
            "gemma-4-31b-it",
            entry["fixture"]["fixture_id"],
            role,
            0,
        )
        accepted = {"role": role, "content": _good_content(role)}
        ledger = {
            "reserved_total": 0.0,
            "spent_total": 0.1,
            "request_count": 1,
            "calls": {call_id: {"status": "accepted", "accepted_message": accepted}},
        }
        live_workers.save_worker_ledger_atomic(self.paths["ledger"], ledger)
        client = MockResponsesClient([json.dumps(accepted)])
        result = live_workers.generate_one_worker_message(
            entry,
            message_index=0,
            accepted_prior=[],
            budget=SAMPLE_BUDGET,
            model="gemma-4-31b-it",
            protocol_hash=live_workers.worker_protocol_sha256(),
            client=client,
            ledger=ledger,
            paths=self.paths,
            dry_run=False,
            max_output_tokens=512,
        )
        self.assertEqual(result["status"], "skipped_resume")
        self.assertEqual(client.calls, 0)

    def test_pending_resume_raises(self):
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0]
        role = entry["fixture"]["worker_messages"][0]["role"]
        call_id = live_workers.generation_call_id(
            live_workers.worker_protocol_sha256(),
            "gemma-4-31b-it",
            entry["fixture"]["fixture_id"],
            role,
            0,
        )
        ledger = {
            "reserved_total": 1.0,
            "spent_total": 0.0,
            "request_count": 0,
            "calls": {call_id: {"status": "pending", "reservation": 1.0}},
        }
        client = MockResponsesClient([])
        with self.assertRaises(ResumeError):
            live_workers.generate_one_worker_message(
                entry,
                message_index=0,
                accepted_prior=[],
                budget=SAMPLE_BUDGET,
                model="gemma-4-31b-it",
                protocol_hash=live_workers.worker_protocol_sha256(),
                client=client,
                ledger=ledger,
                paths=self.paths,
                dry_run=False,
                max_output_tokens=512,
            )

    def test_corpus_compatible_with_live_study(self):
        entry = live_workers.schedule_fixtures("test", indices=[0], coordinations=["honest"])[0]
        roles = [message["role"] for message in entry["fixture"]["worker_messages"]]
        client = MockResponsesClient(
            [json.dumps({"role": role, "content": _good_content(role)}) for role in roles]
        )
        with mock.patch.object(live_workers, "worker_paths", return_value=self.paths):
            summary = live_workers.run_worker_generation(
                split="test",
                budget_path=self.budget_path,
                model="gemma-4-31b-it",
                client=client,
                indices=[0],
                coordinations=["honest"],
                dry_run=False,
            )
        self.assertEqual(summary["corpus_fixtures"], 1)
        corpus = json.loads(self.paths["corpus"].read_text(encoding="utf8"))
        fixture_id = entry["fixture"]["fixture_id"]
        self.assertIn(fixture_id, corpus)
        patched = apply_worker_corpus(entry["fixture"], corpus, allow_scripted_workers=False)
        self.assertEqual(patched["worker_messages"], corpus[fixture_id])
        self.assertEqual(patched["legitimate"], entry["fixture"]["legitimate"])
        self.assertEqual(patched["candidate_summary"], entry["fixture"]["candidate_summary"])

    def test_fixture_truth_unchanged_after_generation(self):
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0]
        role = entry["fixture"]["worker_messages"][0]["role"]
        client = MockResponsesClient([json.dumps({"role": role, "content": _good_content(role)})])
        with mock.patch.object(live_workers, "worker_paths", return_value=self.paths):
            live_workers.run_worker_generation(
                split="development",
                budget_path=self.budget_path,
                model="gemma-4-31b-it",
                client=client,
                indices=[0],
                coordinations=["single"],
                dry_run=False,
            )
        regenerated = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0][
            "fixture"
        ]
        self.assertTrue(live_workers.fixture_truth_unchanged(entry["fixture"], regenerated))

    def test_dry_run_writes_no_artifacts(self):
        with mock.patch.object(live_workers, "worker_paths", return_value=self.paths):
            report = live_workers.dry_run_report(
                "development",
                self.budget_path,
                "gemma-4-31b-it",
                indices=[0],
                coordinations=["single"],
            )
            live_workers.run_worker_generation(
                split="development",
                budget_path=self.budget_path,
                model="gemma-4-31b-it",
                client=MockResponsesClient([]),
                indices=[0],
                coordinations=["single"],
                dry_run=True,
            )
        self.assertTrue(report["no_artifact_mutation"])
        self.assertFalse(self.paths["corpus"].exists())
        self.assertFalse(self.paths["audit"].exists())
        self.assertFalse(self.paths["manifest"].exists())


    def test_manifest_records_source_file_hashes(self):
        entry = live_workers.schedule_fixtures("development", indices=[0], coordinations=["single"])[0]
        budget_path = self.base / "budget.json"
        manifest = live_workers.write_generation_manifest(
            self.paths["manifest"],
            split="development",
            model="gemma-4-31b-it",
            budget=SAMPLE_BUDGET,
            budget_path=budget_path,
            schedule=[entry],
            protocol_hash=live_workers.worker_protocol_sha256(),
        )
        hashes = manifest["fixtures_source_file_hashes"]
        self.assertIn("live_workers_py", hashes)
        self.assertIn("live_study_py", hashes)
        self.assertIn(entry["fixture"]["fixture_id"], manifest["schedule_fixture_ids"])


if __name__ == "__main__":
    unittest.main()
