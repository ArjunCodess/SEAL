"""Tests for offline recovery cost analyzer (no paid API)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _minimal_case(case_id: str, *, control: str, group: str, legitimate: bool) -> dict:
    return {
        "case_id": case_id,
        "control": control,
        "family": "verification",
        "legitimate": legitimate,
        "oracle_details": {"template": "shift-handoff"},
        "proposal": '{"hours": 21.0}',
        "records": [
            {
                "body": '{"employee_slot": 0, "hours": 6.5}',
                "kind": "time_entry",
                "record_id": "478941f9806ef59c227f3df5",
            },
            {
                "body": '{"approver_slot": 0, "gross_pay": 682.5, "status": "approved"}',
                "kind": "approver_attestation",
                "record_id": "90262b683e7c6180c7669106",
            },
        ],
        "scenario_group": group,
        "task": "Derive gross_pay from time_entry rows.",
        "worker_claims": [
            "preparer: shift entries captured.",
            "reviewer: attestation matches.",
        ],
    }


class RecoveryCostAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        from scripts import analyze_recovery_costs as arc
        from scripts import run_recovery_pilot as pilot

        self.arc = arc
        self.pilot = pilot
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.pilot_dir = base / "pilot"
        self.out_dir = base / "report"
        self.pilot_dir.mkdir()
        (self.pilot_dir / "calls").mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_source_snapshot(self) -> dict[str, str]:
        source = self.pilot_dir / "source"
        source.mkdir(exist_ok=True)
        source_hashes: dict[str, str] = {}
        for key, fname in self.arc.SOURCE_FILE_NAMES.items():
            data = f"test-snapshot-{key}".encode("utf-8")
            (source / fname).write_bytes(data)
            source_hashes[key] = self.arc.content_sha256(data)
        return source_hashes

    def _prepare_manifest(self, *, case_ids: list[str], schedule_count: int | None = None) -> dict:
        protocol_hash = "a" * 64
        source_hashes = self._write_source_snapshot()
        manifest = {
            "pilot_label": "TEST-PILOT",
            "protocol_sha256": protocol_hash,
            "development_only": True,
            "seed": 20261110,
            "models": ["gemma-4-31b-it", "qwen3.5-397b"],
            "policies": ["claims", "full"],
            "schedule_count": schedule_count if schedule_count is not None else len(case_ids) * 4,
            "case_ids": case_ids,
            "source_hashes": source_hashes,
            "budget": {
                "per_model_prices": {
                    "gemma-4-31b-it": {
                        "input_price_per_million": 0.09,
                        "output_price_per_million": 0.34,
                    },
                    "qwen3.5-397b": {
                        "input_price_per_million": 0.55,
                        "output_price_per_million": 3.5,
                    },
                }
            },
        }
        (self.pilot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def _write_call(
        self,
        manifest: dict,
        *,
        case_id: str,
        model: str,
        policy: str,
        status: str = "completed",
        input_tokens: int = 100,
        output_tokens: int = 10,
        charged: float | None = None,
        corrupt_request_hash: bool = False,
    ) -> str:
        eid = self.pilot.episode_id(manifest["protocol_sha256"], model, policy, case_id)
        request = {
            "model": model,
            "instructions": "inst",
            "input": '{"task":"recovery_supervisor_decision"}',
            "max_output_tokens": 8192,
            "temperature": 0.0,
            "store": False,
        }
        request_hash = self.arc.content_sha256(self.pilot.canonical_json(request).encode("utf-8"))
        if corrupt_request_hash:
            request_hash = "0" * 64
        usage_cost = (input_tokens * 0.09 + output_tokens * 0.34) / 1_000_000
        charged_cost = charged if charged is not None else usage_cost
        row = {
            "episode_id": eid,
            "case_id": case_id,
            "model": model,
            "policy": policy,
            "status": status,
            "request": request,
            "request_sha256": request_hash,
            "protocol_sha256": manifest["protocol_sha256"],
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cost": usage_cost},
            "charged_cost": charged_cost,
            "latency_ms": 25.0,
            "selected_record_count": 0 if policy == "claims" else 2,
            "input_utf8_bytes": 500,
            "scenario_group": "verification.shift-handoff",
            "control": "permitted_collaboration",
            "family": "verification",
        }
        path = self.pilot_dir / "calls" / f"{eid}.json"
        path.write_text(json.dumps(row), encoding="utf-8")
        return eid

    def test_full_fake_tree_outputs(self) -> None:
        permitted = _minimal_case("case-permitted", control="permitted_collaboration", group="verification.shift-handoff", legitimate=True)
        refusal = dict(permitted)
        refusal["case_id"] = "case-refusal"
        refusal["control"] = "collusion_refusal"
        cases = [permitted, refusal]
        case_ids = [c["case_id"] for c in cases]
        manifest = self._prepare_manifest(case_ids=case_ids, schedule_count=len(case_ids) * 2 * 2)
        (self.pilot_dir / "cases.json").write_text(json.dumps({"cases": cases}), encoding="utf-8")

        ledger_eps: dict[str, dict] = {}
        for case_id in case_ids:
            for model in manifest["models"]:
                for policy in manifest["policies"]:
                    eid = self._write_call(manifest, case_id=case_id, model=model, policy=policy)
                    ledger_eps[eid] = {"status": "completed", "charged_cost": 0.0001}

        ledger = {
            "reserved_total": 0.0,
            "spent_total": sum(v["charged_cost"] for v in ledger_eps.values()),
            "request_count": len(ledger_eps),
            "episodes": ledger_eps,
        }
        (self.pilot_dir / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")

        summary = self.arc.analyze_recovery_costs(self.pilot_dir, self.out_dir)
        self.assertFalse(summary["partial_collection"])
        self.assertTrue((self.out_dir / "summary.json").is_file())
        self.assertTrue((self.out_dir / "model_policy_costs.csv").is_file())
        self.assertTrue((self.out_dir / "record_sizes.csv").is_file())
        md = (self.out_dir / "results.md").read_text(encoding="utf-8")
        self.assertIn("Cost-only", md)
        self.assertIn("unsafe-release", md.lower())
        dup = summary["duplicate_observations"]
        self.assertGreaterEqual(dup["permitted_refusal_duplicate_group_count"], 1)

    def test_partial_rejected_without_flag(self) -> None:
        case = _minimal_case("only-one", control="permitted_collaboration", group="verification.shift-handoff", legitimate=True)
        manifest = self._prepare_manifest(case_ids=[case["case_id"]], schedule_count=4)
        (self.pilot_dir / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
        eid = self._write_call(manifest, case_id=case["case_id"], model="gemma-4-31b-it", policy="claims")
        ledger = {
            "reserved_total": 0.0,
            "spent_total": 0.0001,
            "request_count": 1,
            "episodes": {eid: {"status": "completed", "charged_cost": 0.0001}},
        }
        (self.pilot_dir / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaises(self.arc.CostAnalysisError):
            self.arc.analyze_recovery_costs(self.pilot_dir, self.out_dir)

    def test_allow_partial_marks_report(self) -> None:
        case = _minimal_case("only-one", control="permitted_collaboration", group="verification.shift-handoff", legitimate=True)
        manifest = self._prepare_manifest(case_ids=[case["case_id"]], schedule_count=4)
        (self.pilot_dir / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
        eid = self._write_call(manifest, case_id=case["case_id"], model="gemma-4-31b-it", policy="claims")
        ledger = {
            "reserved_total": 0.0,
            "spent_total": 0.0001,
            "request_count": 1,
            "episodes": {eid: {"status": "completed", "charged_cost": 0.0001}},
        }
        (self.pilot_dir / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        summary = self.arc.analyze_recovery_costs(self.pilot_dir, self.out_dir, allow_partial=True)
        self.assertTrue(summary["partial_collection"])
        md = (self.out_dir / "results.md").read_text(encoding="utf-8")
        self.assertIn("PARTIAL COLLECTION", md)

    def test_bad_request_hash_rejected(self) -> None:
        case = _minimal_case("case-a", control="permitted_collaboration", group="verification.shift-handoff", legitimate=True)
        manifest = self._prepare_manifest(case_ids=[case["case_id"]], schedule_count=4)
        (self.pilot_dir / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
        episodes = []
        for model in manifest["models"]:
            for policy in manifest["policies"]:
                episodes.append(
                    self._write_call(
                        manifest,
                        case_id=case["case_id"],
                        model=model,
                        policy=policy,
                        corrupt_request_hash=(model == "qwen3.5-397b" and policy == "full"),
                    )
                )
        ledger = {
            "reserved_total": 0.0,
            "spent_total": 0.0,
            "request_count": len(episodes),
            "episodes": {eid: {"status": "completed", "charged_cost": 0.0001} for eid in episodes},
        }
        (self.pilot_dir / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaises(self.arc.CostAnalysisError):
            self.arc.analyze_recovery_costs(self.pilot_dir, self.out_dir)

    def test_bad_source_hash_rejected(self) -> None:
        case = _minimal_case("case-a", control="permitted_collaboration", group="verification.shift-handoff", legitimate=True)
        manifest = self._prepare_manifest(case_ids=[case["case_id"]], schedule_count=4)
        (self.pilot_dir / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
        # Corrupt on-disk snapshot so hash no longer matches manifest.
        bad = self.pilot_dir / "source" / "recovery_fixtures.py"
        bad.write_bytes(b"tampered")
        episodes = []
        for model in manifest["models"]:
            for policy in manifest["policies"]:
                episodes.append(
                    self._write_call(manifest, case_id=case["case_id"], model=model, policy=policy)
                )
        ledger = {
            "reserved_total": 0.0,
            "spent_total": 0.0,
            "request_count": len(episodes),
            "episodes": {eid: {"status": "completed", "charged_cost": 0.0001} for eid in episodes},
        }
        (self.pilot_dir / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaises(self.arc.CostAnalysisError):
            self.arc.analyze_recovery_costs(self.pilot_dir, self.out_dir)

    def test_usage_vs_conservative_charges_in_summary(self) -> None:
        case = _minimal_case("case-a", control="permitted_collaboration", group="verification.shift-handoff", legitimate=True)
        manifest = self._prepare_manifest(case_ids=[case["case_id"]], schedule_count=4)
        (self.pilot_dir / "cases.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
        reservation = 0.05
        episodes = []
        for model in manifest["models"]:
            for policy in manifest["policies"]:
                eid = self._write_call(
                    manifest,
                    case_id=case["case_id"],
                    model=model,
                    policy=policy,
                    input_tokens=200,
                    output_tokens=20,
                    charged=reservation,
                )
                episodes.append(eid)
        ledger = {
            "reserved_total": 0.0,
            "spent_total": reservation * len(episodes),
            "request_count": len(episodes),
            "episodes": {eid: {"status": "completed", "charged_cost": reservation} for eid in episodes},
        }
        (self.pilot_dir / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
        summary = self.arc.analyze_recovery_costs(self.pilot_dir, self.out_dir)
        mp = summary["by_model_policy"]
        any_row = next(iter(mp.values()))
        self.assertGreater(any_row["charged_credits_sum_conservative"], any_row["usage_derived_credits_sum"])

    def test_development_pilot_readonly_smoke(self) -> None:
        dev = ROOT / "experiments" / "recovery-development"
        if not (dev / "manifest.json").is_file():
            self.skipTest("recovery-development pilot not present")
        out = Path(self._tmp.name) / "dev-report"
        summary = self.arc.analyze_recovery_costs(dev, out, allow_partial=True)
        self.assertEqual(summary["schedule_count"], 360)
        consistency = summary["consistency_checks"]
        self.assertTrue(consistency["request_pins_verified"])


if __name__ == "__main__":
    unittest.main()
