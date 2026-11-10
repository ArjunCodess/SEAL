"""Offline contract tests for the budgeted recovery replay framework."""
from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from src import budgeted_recovery as br


def _make_fixture(
    *,
    workflow_id: str = "wf-001",
    visible: int = 0,
    record_count: int = 3,
    missing_id: str | None = None,
    compromised_id: str | None = None,
    retrieval_cost: float = 0.1,
    provenance: str = "development",
) -> tuple[br.EvidenceCase, br.TrustedArchive]:
    records: list[tuple[str, str, str]] = []
    for i in range(record_count):
        rid = f"rec{i:02d}"
        kind = "measurement" if i == 0 else f"supporting-{i}"
        body = json.dumps({"slot": i, "value": 10 + i})
        records.append((rid, kind, body))
    missing = frozenset({missing_id}) if missing_id else frozenset()
    compromised = frozenset({compromised_id}) if compromised_id else frozenset()
    manifest, signing_key = br.build_manifest(
        workflow_id, records, missing_ids=missing, compromised_ids=compromised
    )
    bodies = {rid: body for rid, _k, body in records}
    archive = br.TrustedArchive.build(
        workflow_id,
        manifest,
        bodies,
        signing_key=signing_key,
        compromised_record_ids=compromised,
    )
    visible_map = {records[i][0]: records[i][2] for i in range(min(visible, record_count))}
    case = br.EvidenceCase(
        workflow_id=workflow_id,
        task="Verify measurement supporting evidence before release.",
        proposal="Publish the verified summary.",
        worker_claims=("preparer: ready", "reviewer: ok"),
        manifest=manifest,
        initial_visible_bodies=visible_map,
        retrieval_seconds_per_record=retrieval_cost,
        provenance=provenance,
    )
    return case, archive


@dataclass
class SyntheticSupervisor:
    prefer_release_when_verified: bool = True
    token_credits: float = 0.001
    reserve: float | None = None
    release_after_acquisitions: int | None = None

    def reserve_token_credits(self) -> float:
        return self.reserve if self.reserve is not None else self.token_credits

    def decide(self, observation: dict, *, ledger: br.CostLedger) -> br.SupervisorDecision:
        acquired = observation.get("acquired_records", [])
        verified = [row for row in acquired if row.get("verified")]
        if self.release_after_acquisitions is not None:
            if len(acquired) >= self.release_after_acquisitions and verified:
                return br.SupervisorDecision(
                    action="release", reason="synthetic early release", token_credits=self.token_credits
                )
            return br.SupervisorDecision(action="acquire", reason="need more evidence", token_credits=self.token_credits)
        if self.prefer_release_when_verified and verified and not observation.get("missing_record_metadata"):
            return br.SupervisorDecision(action="release", reason="synthetic release", token_credits=self.token_credits)
        return br.SupervisorDecision(action="hold", reason="synthetic hold", token_credits=self.token_credits)


class BudgetedRecoveryTests(unittest.TestCase):
    def test_replay_environment_label(self) -> None:
        case, archive = _make_fixture()
        sup = SyntheticSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=10.0, token_credits=1.0)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.CHRONOLOGICAL,
            budget,
            sup,
            seed=1,
        )
        self.assertEqual(result.environment, br.REPLAY_ENVIRONMENT_LABEL)
        obs = br.policy_observation(case, result.acquisitions)
        self.assertEqual(obs["environment"], br.REPLAY_ENVIRONMENT_LABEL)

    def test_policy_observation_excludes_oracle_fields(self) -> None:
        case, archive = _make_fixture()
        blob = json.dumps(br.policy_observation(case, ()))
        for token in ("legitimate", "control", "oracle", "scenario_group", "case_id"):
            self.assertNotIn(token, blob)
        _ = archive

    def test_separated_cost_ledger(self) -> None:
        ledger = br.CostLedger()
        ledger.charge_retrieval(0.2)
        ledger.charge_review(1.5)
        ledger.charge_tokens(0.01)
        self.assertAlmostEqual(ledger.retrieval_seconds, 0.2)
        self.assertAlmostEqual(ledger.review_seconds, 1.5)
        self.assertAlmostEqual(ledger.token_credits, 0.01)

    def test_token_reserve_enforcement(self) -> None:
        ledger = br.CostLedger()
        ledger.begin_token_reserve(0.05)
        ledger.settle_token_reserve(0.05, 0.03)
        self.assertAlmostEqual(ledger.token_credits, 0.03)
        ledger.begin_token_reserve(0.01)
        with self.assertRaises(ValueError):
            ledger.settle_token_reserve(0.01, 0.02)

    def test_digest_and_signature_verification(self) -> None:
        case, archive = _make_fixture(record_count=1)
        entry = case.manifest[0]
        ok, regime = br.verify_acquired_body(
            workflow_id=case.workflow_id,
            entry=entry,
            body=archive.bodies[entry.record_id],
            archive=archive,
        )
        self.assertTrue(ok)
        self.assertEqual(regime, br.ArchiveRegime.NORMAL)

    def test_identity_envelope_rejects_replay(self) -> None:
        case, archive = _make_fixture(record_count=2)
        entry = case.manifest[0]
        body = archive.bodies[entry.record_id]
        ok, regime = br.verify_acquired_body(
            workflow_id="other-workflow",
            entry=entry,
            body=body,
            archive=archive,
        )
        self.assertFalse(ok)
        self.assertEqual(regime, br.ArchiveRegime.TAMPERING)
        ok2, regime2 = br.verify_acquired_body(
            workflow_id=case.workflow_id,
            entry=entry,
            body=body,
            archive=archive,
            kind="wrong-kind",
        )
        self.assertFalse(ok2)
        self.assertEqual(regime2, br.ArchiveRegime.TAMPERING)

    def test_initial_visible_replay_rejected(self) -> None:
        case, archive = _make_fixture(record_count=2, visible=1)
        wrong_body = archive.bodies["rec01"]
        case = br.EvidenceCase(
            workflow_id=case.workflow_id,
            task=case.task,
            proposal=case.proposal,
            worker_claims=case.worker_claims,
            manifest=case.manifest,
            initial_visible_bodies={"rec00": wrong_body},
            retrieval_seconds_per_record=case.retrieval_seconds_per_record,
        )
        sup = SyntheticSupervisor(prefer_release_when_verified=False)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.CHRONOLOGICAL,
            br.RecoveryBudget(retrieval_seconds=1.0),
            sup,
        )
        visible = next(e for e in result.events if e.get("kind") == "initial_visible")
        self.assertFalse(visible["verified"])
        self.assertIn("integrity_failure", [e.get("kind") for e in result.events])

    def test_trusted_archive_requires_signing_key_for_manifest(self) -> None:
        case, archive = _make_fixture(record_count=1)
        with self.assertRaises(ValueError):
            br.TrustedArchive.build(case.workflow_id, case.manifest, archive.bodies, signing_key=None)

    def test_tampering_regime_on_wrong_body(self) -> None:
        case, archive = _make_fixture(record_count=1)
        entry = case.manifest[0]
        ok, regime = br.verify_acquired_body(
            workflow_id=case.workflow_id,
            entry=entry,
            body='{"tampered": true}',
            archive=archive,
        )
        self.assertFalse(ok)
        self.assertEqual(regime, br.ArchiveRegime.TAMPERING)

    def test_compromised_signer_crypto_passes_evaluator_flags(self) -> None:
        case, archive = _make_fixture(record_count=2, compromised_id="rec00")
        entry = case.manifest[0]
        ok, regime = br.verify_acquired_body(
            workflow_id=case.workflow_id,
            entry=entry,
            body=archive.bodies[entry.record_id],
            archive=archive,
        )
        self.assertTrue(ok)
        self.assertEqual(regime, br.ArchiveRegime.NORMAL)
        self.assertIn("rec00", archive.compromised_record_ids)

    def test_compromised_signer_not_gate_hold_without_revocation(self) -> None:
        case, archive = _make_fixture(record_count=1, compromised_id="rec00")
        sup = SyntheticSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=1.0)
        gate = br.ActionGate(require_all_manifest_rows_resolved=False)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            budget,
            sup,
            gate=gate,
        )
        self.assertEqual(result.gate_action, "release")
        self.assertEqual(result.evaluator_outcome["compromised_signer_records"], ["rec00"])

    def test_compromised_signer_hold_on_observable_revocation(self) -> None:
        case, archive = _make_fixture(record_count=1, compromised_id="rec00")
        gate = br.ActionGate(require_observable_revocation_for_compromise=True)
        events = [{"kind": "signer_revocation", "record_id": "rec00"}]
        row, _ = br.fetch_record(case, "rec00", archive)
        assert row is not None
        gate_result = gate.evaluate(case, (row,), events)
        self.assertEqual(gate_result["action"], "hold")

    def test_archive_unavailable_holds(self) -> None:
        case, archive = _make_fixture(visible=0)
        archive = br.TrustedArchive(
            public_key_b64=archive.public_key_b64,
            bodies=archive.bodies,
            signatures=archive.signatures,
            available=False,
        )
        sup = SyntheticSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=1.0)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            budget,
            sup,
        )
        kinds = [event["kind"] for event in result.events]
        self.assertIn("archive_unavailable", kinds)
        self.assertEqual(result.gate_action, "hold")
        self.assertEqual(result.terminal, "hold")

    def test_absent_record_failure_to_record(self) -> None:
        case, archive = _make_fixture(missing_id="rec02")
        entry = next(e for e in case.manifest if e.record_id == "rec02")
        self.assertTrue(entry.authenticated_missing)
        row, event = br.fetch_record(case, "rec02", archive)
        self.assertIsNone(row)
        self.assertEqual(event["kind"], "failure_to_record")

    def test_budget_exhaustion_logged(self) -> None:
        case, archive = _make_fixture(record_count=4, retrieval_cost=0.5)
        sup = SyntheticSupervisor(prefer_release_when_verified=False)
        budget = br.RecoveryBudget(retrieval_seconds=0.75)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            budget,
            sup,
        )
        self.assertTrue(result.budget_exhausted)
        self.assertIn("budget_exhausted", [e.get("kind") for e in result.events])
        acquisitions = [e for e in result.events if e.get("kind") in {"acquisition", "integrity_failure"}]
        self.assertLessEqual(len(acquisitions), 1)

    def test_exact_budget_not_exhausted_when_no_candidates(self) -> None:
        case, archive = _make_fixture(record_count=1, retrieval_cost=0.5)
        sup = SyntheticSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=0.5)
        result = br.run_trajectory(case, archive, br.AcquisitionPolicyName.FULL, budget, sup)
        self.assertFalse(result.budget_exhausted)

    def test_zero_token_budget_skips_supervisor_call(self) -> None:
        case, archive = _make_fixture(record_count=1)
        sup = SyntheticSupervisor(reserve=0.01)
        budget = br.RecoveryBudget(retrieval_seconds=1.0, token_credits=0.0)
        result = br.run_trajectory(case, archive, br.AcquisitionPolicyName.FULL, budget, sup)
        self.assertIn("supervisor_skipped", [e.get("kind") for e in result.events])

    def test_zero_review_budget_skips_supervisor(self) -> None:
        case, archive = _make_fixture(record_count=1)
        case = br.EvidenceCase(
            workflow_id=case.workflow_id,
            task=case.task,
            proposal=case.proposal,
            worker_claims=case.worker_claims,
            manifest=case.manifest,
            review_seconds_per_supervisor_decision=0.01,
        )
        sup = SyntheticSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=1.0, review_seconds=0.0)
        result = br.run_trajectory(case, archive, br.AcquisitionPolicyName.FULL, budget, sup)
        self.assertIn("supervisor_skipped", [e.get("kind") for e in result.events])

    def test_selective_early_stop(self) -> None:
        case, archive = _make_fixture(record_count=3, retrieval_cost=0.1)
        sup = SyntheticSupervisor(release_after_acquisitions=1)
        budget = br.RecoveryBudget(retrieval_seconds=1.0)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.CHRONOLOGICAL,
            budget,
            sup,
            gate=br.ActionGate(require_all_manifest_rows_resolved=False),
        )
        self.assertEqual(result.gate_action, "release")
        self.assertEqual(len(result.acquisitions), 1)
        self.assertIn("unresolved_manifest_rows", [e.get("kind") for e in result.events])

    def test_same_gate_blocks_release_on_integrity_failure(self) -> None:
        case, archive = _make_fixture(record_count=2)
        sup = SyntheticSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=5.0)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            budget,
            sup,
            tamper_on_fetch={"rec01": '{"bad": 1}'},
        )
        self.assertEqual(result.gate_action, "hold")
        terminal = next(e for e in result.events if e.get("kind") == "terminal")
        self.assertEqual(terminal["action"], "hold")

    def test_policies_acquire_different_orders(self) -> None:
        case, archive = _make_fixture(record_count=3, visible=0)
        case = br.EvidenceCase(
            workflow_id=case.workflow_id,
            task="Review supporting-2 cross-check against supporting-1 notes before release.",
            proposal=case.proposal,
            worker_claims=case.worker_claims,
            manifest=case.manifest,
            initial_visible_bodies=case.initial_visible_bodies,
            retrieval_seconds_per_record=case.retrieval_seconds_per_record,
        )
        sup = SyntheticSupervisor(prefer_release_when_verified=False, release_after_acquisitions=99)
        budget = br.RecoveryBudget(retrieval_seconds=10.0, token_credits=1.0)
        chrono = br.run_trajectory(case, archive, br.AcquisitionPolicyName.CHRONOLOGICAL, budget, sup)
        obligation = br.run_trajectory(case, archive, br.AcquisitionPolicyName.OBLIGATION, budget, sup)
        random_a = br.run_trajectory(case, archive, br.AcquisitionPolicyName.RANDOM, budget, sup, seed=99)
        random_b = br.run_trajectory(case, archive, br.AcquisitionPolicyName.RANDOM, budget, sup, seed=99)
        chrono_ids = [row.record_id for row in chrono.acquisitions]
        oblig_ids = [row.record_id for row in obligation.acquisitions]
        self.assertEqual(chrono_ids, ["rec00", "rec01", "rec02"])
        self.assertEqual([row.record_id for row in random_a.acquisitions], [row.record_id for row in random_b.acquisitions])
        self.assertNotEqual(chrono_ids, oblig_ids)

    def test_full_policy_acquires_all_before_single_supervisor_decision(self) -> None:
        case, archive = _make_fixture(record_count=3)
        call_log: list[str] = []

        class CountingSupervisor(SyntheticSupervisor):
            def decide(self, observation: dict, *, ledger: br.CostLedger) -> br.SupervisorDecision:
                call_log.append("decide")
                return super().decide(observation, ledger=ledger)

        sup = CountingSupervisor()
        budget = br.RecoveryBudget(retrieval_seconds=5.0, token_credits=1.0)
        result = br.run_trajectory(case, archive, br.AcquisitionPolicyName.FULL, budget, sup)
        self.assertEqual(len(result.acquisitions), 3)
        self.assertEqual(call_log.count("decide"), 1)

    def test_learned_ranker_fit_and_run(self) -> None:
        cases: list[br.EvidenceCase] = []
        archives: list[br.TrustedArchive] = []
        labels: list[br.DevelopmentLabel] = []
        for idx in range(4):
            case, archive = _make_fixture(workflow_id=f"wf-learn-{idx}", record_count=3)
            cases.append(case)
            archives.append(archive)
            for entry in case.manifest:
                helps = entry.kind.startswith("measurement")
                labels.append(br.DevelopmentLabel(case.workflow_id, entry.record_id, helps))
        ranker = br.fit_learned_ranker(cases, labels, random_state=0)
        self.assertEqual(ranker.fitted_on, "development_only")
        case, archive = cases[0], archives[0]
        sup = SyntheticSupervisor(prefer_release_when_verified=False, release_after_acquisitions=99)
        budget = br.RecoveryBudget(retrieval_seconds=1.0, token_credits=1.0)
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.LEARNED,
            budget,
            sup,
            ranker=ranker,
        )
        self.assertGreaterEqual(len(result.acquisitions), 1)

    def test_fit_learned_ranker_rejects_missing_labels(self) -> None:
        case, _ = _make_fixture(record_count=2)
        labels = [br.DevelopmentLabel(case.workflow_id, case.manifest[0].record_id, True)]
        with self.assertRaises(ValueError):
            br.fit_learned_ranker([case], labels)

    def test_fit_learned_ranker_rejects_non_development(self) -> None:
        case, _ = _make_fixture(record_count=1, provenance="held_out")
        labels = [br.DevelopmentLabel(case.workflow_id, case.manifest[0].record_id, True)]
        with self.assertRaises(ValueError):
            br.fit_learned_ranker([case], labels)

    def test_ranker_features_exclude_forbidden_keys(self) -> None:
        case, _ = _make_fixture()
        entry = case.manifest[0]
        feats = br.ranker_features(case, entry, set())
        for key in feats:
            self.assertNotIn(key, br._FORBIDDEN_FEATURE_TOKENS)

    def test_parse_supervisor_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            br.parse_supervisor_decision({"action": "explode", "reason": "x", "token_credits": 0.0})

    def test_parse_supervisor_nan_cost(self) -> None:
        with self.assertRaises(ValueError):
            br.parse_supervisor_decision({"action": "hold", "reason": "x", "token_credits": float("nan")})

    def test_indistinguishability_witness(self) -> None:
        case_a, archive_a = _make_fixture(workflow_id="wf-indist-a", record_count=2, visible=1)
        case_b, archive_b = _make_fixture(workflow_id="wf-indist-b", record_count=2, visible=1)
        case_b = br.EvidenceCase(
            workflow_id=case_b.workflow_id,
            task=case_a.task,
            proposal=case_a.proposal,
            worker_claims=case_a.worker_claims,
            manifest=case_b.manifest,
            initial_visible_bodies=case_b.initial_visible_bodies,
            retrieval_seconds_per_record=case_a.retrieval_seconds_per_record,
        )
        witness = br.indistinguishability_witness(
            case_a,
            case_b,
            archive_a,
            archive_b,
            br.RecoveryBudget(retrieval_seconds=0.2),
            policy=br.AcquisitionPolicyName.FULL,
        )
        self.assertIn("identical_observations", witness)
        self.assertIn("not a novelty claim", witness["note"])

    def test_supervisor_error_terminal(self) -> None:
        class BrokenSupervisor:
            def reserve_token_credits(self) -> float:
                return 0.0

            def decide(self, observation, *, ledger):
                raise RuntimeError("synthetic failure")

        case, archive = _make_fixture()
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            br.RecoveryBudget(retrieval_seconds=1.0, token_credits=1.0),
            BrokenSupervisor(),
        )
        self.assertEqual(result.terminal, "error")
        self.assertEqual(result.gate_action, "hold")

    def test_measured_retrieval_callback(self) -> None:
        case, archive = _make_fixture(record_count=1)

        def cost_fn(c: br.EvidenceCase, rid: str) -> tuple[float, str]:
            return 0.07, "measured_simulator"

        sup = SyntheticSupervisor()
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            br.RecoveryBudget(retrieval_seconds=1.0, token_credits=1.0),
            sup,
            retrieval_cost_fn=cost_fn,
        )
        acq = next(e for e in result.events if e.get("kind") == "acquisition")
        self.assertEqual(acq["retrieval_label"], "measured_simulator")
        self.assertAlmostEqual(acq["retrieval_seconds"], 0.07)

    def test_terminal_events_logged(self) -> None:
        case, archive = _make_fixture()
        sup = SyntheticSupervisor()
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.CHRONOLOGICAL,
            br.RecoveryBudget(retrieval_seconds=2.0, token_credits=1.0),
            sup,
        )
        terminals = [e for e in result.events if e.get("kind") == "terminal"]
        self.assertEqual(len(terminals), 1)
        self.assertTrue(terminals[0].get("terminal"))
        self.assertIn(terminals[0]["action"], {"release", "hold"})


if __name__ == "__main__":
    unittest.main()
