"""Tests for recovery development pilot harness (no paid API)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class FakeResponsesClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def create(self, **payload):
        self.calls.append(payload)
        return {
            "status": "completed",
            "_http_status": 200,
            "output_text": self.text,
            "usage": {"input_tokens": 1200, "output_tokens": 80},
            "_latency_ms": 12.5,
        }


class RecoveryPilotTests(unittest.TestCase):
    def setUp(self) -> None:
        from scripts import run_recovery_pilot as pilot

        self.pilot = pilot
        self._tmp = tempfile.TemporaryDirectory()
        self.output = Path(self._tmp.name) / "recovery_out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_schedule_size(self) -> None:
        fx = self.pilot._import_fixtures()
        cases = fx.build_cases(20261110)
        schedule = self.pilot.build_schedule(cases, self.pilot.MODELS, self.pilot.POLICIES)
        self.assertEqual(len(cases), 36)
        self.assertEqual(len(schedule), 360)

    def test_observation_excludes_forbidden_metadata(self) -> None:
        fx = self.pilot._import_fixtures()
        case = fx.build_cases(20261110)[0]
        selected = self.pilot.select_policy_records("full", case, seed=20261110)
        meta = [self.pilot.record_metadata(row) for row in case.records]
        observation = self.pilot.build_llm_observation(
            case, "full", selected, archive_metadata=meta
        )
        payload = json.dumps(observation)
        self.assertNotIn(case.case_id, payload)
        self.assertNotIn(case.control, payload)
        self.assertNotIn(case.scenario_group, payload)
        self.assertNotIn('"legitimate"', payload)
        self.assertNotIn('"oracle_details"', payload)
        for key in self.pilot.FORBIDDEN_PROMPT_KEYS:
            self.assertNotIn(key, observation)

    def test_policy_selection_rules(self) -> None:
        fx = self.pilot._import_fixtures()
        case = fx.build_cases(20261110)[0]
        claims = self.pilot.select_policy_records("claims", case, seed=20261110)
        self.assertEqual(claims, ())
        chrono = self.pilot.select_policy_records("chronological2", case, seed=20261110)
        self.assertEqual(len(chrono), 2)
        self.assertEqual(chrono[0].record_id, case.records[0].record_id)
        full = self.pilot.select_policy_records("full", case, seed=20261110)
        self.assertEqual(len(full), len(case.records))
        random_a = self.pilot.select_policy_records("random2", case, seed=20261110)
        random_b = self.pilot.select_policy_records("random2", case, seed=20261110)
        self.assertEqual([row.record_id for row in random_a], [row.record_id for row in random_b])
        obligation = self.pilot.select_policy_records("obligation2", case, seed=20261110)
        self.assertLessEqual(len(obligation), 2)
        self.assertGreaterEqual(len(obligation), 1)

    def test_archive_sign_and_verify(self) -> None:
        fx = self.pilot._import_fixtures()
        cases = fx.build_cases(20261110)
        archive_index = self.pilot.build_archive_index(cases)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.generate()
        signatures = self.pilot.sign_archive_records(archive_index, private_key)
        public_key = private_key.public_key()
        case = cases[0]
        record = case.records[0]
        sig_key = self.pilot.archive_record_key(case.case_id, record.record_id)
        self.pilot.verify_record_signature(
            body=record.body,
            signature_b64=signatures[sig_key],
            public_key=public_key,
        )
        with self.assertRaises(ValueError):
            self.pilot.verify_record_signature(
                body=record.body + " ",
                signature_b64=signatures[sig_key],
                public_key=public_key,
            )

    def test_parse_recovery_decision(self) -> None:
        ok = self.pilot.parse_recovery_decision(
            json.dumps({"action": "hold", "reason": "missing attestation"})
        )
        self.assertEqual(ok["action"], "hold")
        with self.assertRaises(ValueError):
            self.pilot.parse_recovery_decision(json.dumps({"action": "publish", "reason": "x"}))
        with self.assertRaises(ValueError):
            self.pilot.parse_recovery_decision(json.dumps({"action": "hold"}))

    def test_fake_sink_outcomes(self) -> None:
        fx = self.pilot._import_fixtures()
        case = fx.build_cases(20261110)[0]
        legit = fx.oracle(case)
        release = self.pilot.execute_fake_sink(case, {"action": "release", "reason": "ok"})
        assert release is not None
        self.assertTrue(release["executed"])
        self.assertEqual(release["legitimate_completion"], legit)
        self.assertEqual(release["unsafe_execution"], not legit)
        hold = self.pilot.execute_fake_sink(case, {"action": "hold", "reason": "wait"})
        assert hold is not None
        self.assertFalse(hold["executed"])
        self.assertFalse(hold["unsafe_execution"])
        self.assertIsNone(self.pilot.execute_fake_sink(case, None))

    def test_prepare_makes_no_api_calls(self) -> None:
        report = self.pilot.prepare_pilot(self.output, seed=20261110)
        self.assertTrue(report["no_api_calls"])
        paths = self.pilot.paths_for(self.output)
        self.assertTrue(paths["manifest"].is_file())
        self.assertTrue(paths["protocol"].is_file())
        self.assertTrue(paths["archive"].is_file())
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["pilot_label"], self.pilot.PILOT_LABEL)
        self.assertEqual(manifest["schedule_count"], 360)

    def test_run_with_fake_client_and_analyze(self) -> None:
        self.pilot.prepare_pilot(self.output, seed=20261110)
        fx = self.pilot._import_fixtures()
        case = fx.build_cases(20261110)[0]
        legit = fx.oracle(case)
        action = "release" if legit else "hold"
        client = FakeResponsesClient(json.dumps({"action": action, "reason": "unit test"}))
        with mock.patch.dict(os.environ, {}, clear=True):
            summary = self.pilot.run_pilot(self.output, client=client, workers=2)
        self.assertGreaterEqual(summary["completed"], 1)
        analysis = self.pilot.analyze_pilot(self.output)
        self.assertIn("breakdown_by_model_policy_control_family", analysis)
        self.assertTrue(analysis["no_pooled_model_headline"])
        results_md = self.output / "analysis" / "results.md"
        self.assertTrue(results_md.is_file())
        self.assertIn("feasibility pilot", results_md.read_text(encoding="utf-8"))

    def test_invalid_response_not_treated_as_hold(self) -> None:
        self.pilot.prepare_pilot(self.output, seed=20261110)
        client = FakeResponsesClient('{"action":"maybe","reason":"nope"}')
        first = self.pilot.run_pilot(self.output, client=client, workers=1)
        self.assertGreaterEqual(first["completed"], 0)
        paths = self.pilot.paths_for(self.output)
        rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths["calls"].glob("*.json")]
        invalid = [row for row in rows if row.get("status") == "invalid"]
        self.assertTrue(invalid)
        for row in invalid:
            self.assertIsNone(row.get("execution_outcome"))

    def test_resume_rejects_pending(self) -> None:
        self.pilot.prepare_pilot(self.output, seed=20261110)
        paths = self.pilot.paths_for(self.output)
        ledger = {
            "reserved_total": 0.01,
            "spent_total": 0.0,
            "request_count": 0,
            "episodes": {"pending-episode": {"status": "pending", "reservation": 0.01}},
        }
        paths["ledger"].write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaises(self.pilot.ResumeError):
            self.pilot.run_pilot(self.output, client=FakeResponsesClient('{"action":"hold","reason":"x"}'))

    def test_concurrent_ledger_reservation(self) -> None:
        book = self.pilot.LedgerBook(
            self.output / "ledger.json",
            {"reserved_total": 0.0, "spent_total": 0.0, "request_count": 0, "episodes": {}},
        )
        reasons = [book.try_reserve(f"e{i}", 0.001) for i in range(5)]
        self.assertIsNone(reasons[0])
        snap = book.snapshot()
        self.assertAlmostEqual(snap["reserved_total"], 0.005)

    def test_prepare_resume_preserves_archive(self) -> None:
        self.pilot.prepare_pilot(self.output)
        paths = self.pilot.paths_for(self.output)
        original = {name: paths[name].read_bytes() for name in ("manifest", "protocol", "archive")}
        self.pilot.prepare_pilot(self.output)
        self.assertEqual(original, {name: paths[name].read_bytes() for name in original})
        with self.assertRaises(self.pilot.ResumeError):
            self.pilot.prepare_pilot(self.output, seed=20261111)
        self.assertEqual(original, {name: paths[name].read_bytes() for name in original})

    def test_collect_rejects_changed_sources_and_trust_root(self) -> None:
        self.pilot.prepare_pilot(self.output)
        client = FakeResponsesClient('{"action":"release","reason":"test"}')
        with mock.patch.object(self.pilot, "source_hashes", return_value={}):
            with self.assertRaises(self.pilot.ResumeError):
                self.pilot.run_pilot(self.output, client=client)
        paths = self.pilot.paths_for(self.output)
        doc = json.loads(paths["archive"].read_text())
        doc["signatures"].pop(next(iter(doc["signatures"])))
        paths["archive"].write_text(json.dumps(doc))
        with self.assertRaises(self.pilot.ResumeError):
            self.pilot.run_pilot(self.output, client=client)
        self.assertEqual(client.calls, [])

    def test_output_cannot_cover_frozen_inputs(self) -> None:
        for target in (ROOT, ROOT.parent, ROOT / "data/study/pilot", ROOT / "src/pilot"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.pilot.paths_for(target)


if __name__ == "__main__":
    unittest.main()
