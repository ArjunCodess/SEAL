"""Offline tests for recovery attack plans, auditors, and queue simulator."""
from __future__ import annotations

import json
import unittest

from src import budgeted_recovery as br
from src import recovery_attacks as ra


def _make_fixture(
    *,
    workflow_id: str = "wf-attack",
    record_count: int = 3,
    visible_ids: frozenset[str] = frozenset(),
    retrieval_cost: float = 0.1,
) -> tuple[br.EvidenceCase, br.TrustedArchive]:
    records: list[tuple[str, str, str]] = []
    for i in range(record_count):
        rid = f"rec{i:02d}"
        kind = "measurement" if i == 0 else f"supporting-{i}"
        body = json.dumps({"slot": i, "value": 10 + i})
        records.append((rid, kind, body))
    manifest, signing_key = br.build_manifest(workflow_id, records)
    bodies = {rid: body for rid, _k, body in records}
    archive = br.TrustedArchive.build(workflow_id, manifest, bodies, signing_key=signing_key)
    visible_map = {rid: bodies[rid] for rid in visible_ids if rid in bodies}
    case = br.EvidenceCase(
        workflow_id=workflow_id,
        task="Verify measurement supporting evidence before release.",
        proposal="Publish the verified summary.",
        worker_claims=("preparer: ready",),
        manifest=manifest,
        initial_visible_bodies=visible_map,
        retrieval_seconds_per_record=retrieval_cost,
    )
    return case, archive


class PermissiveSupervisor:
    def reserve_token_credits(self) -> float:
        return 0.0

    def decide(self, observation, *, ledger):
        ra.audit_supervisor_observation(observation)
        return {"action": "release", "reason": "test release", "token_credits": 0.0}


class StrictSupervisor:
    def reserve_token_credits(self) -> float:
        return 0.0

    def decide(self, observation, *, ledger):
        ra.audit_supervisor_observation(observation)
        missing = observation.get("missing_record_metadata") or []
        if missing:
            return {"action": "hold", "reason": "missing metadata", "token_credits": 0.0}
        return {"action": "release", "reason": "all visible", "token_credits": 0.0}


class RecoveryAttacksTests(unittest.TestCase):
    def test_random_omission_is_deterministic(self) -> None:
        case, _ = _make_fixture(record_count=5)
        a = ra.plan_random_omission(case, seed=42, hide_fraction=0.4)
        b = ra.plan_random_omission(case, seed=42, hide_fraction=0.4)
        self.assertEqual(a.hidden_record_ids, b.hidden_record_ids)
        c = ra.plan_random_omission(case, seed=43, hide_fraction=0.4)
        self.assertNotEqual(a.hidden_record_ids, c.hidden_record_ids)

    def test_omission_plan_strips_initial_visibility(self) -> None:
        case, archive = _make_fixture(visible_ids=frozenset({"rec00", "rec01"}))
        plan = ra.OmissionAttackPlan(hidden_record_ids=frozenset({"rec01"}))
        attacked = ra.apply_omission_plan(case, plan, archives_bodies=archive.bodies)
        self.assertIn("rec00", attacked.initial_visible_bodies)
        self.assertNotIn("rec01", attacked.initial_visible_bodies)

    def test_exact_auditor_matches_brute_force_small(self) -> None:
        case, archive = _make_fixture(record_count=3, visible_ids=frozenset({"rec00", "rec01", "rec02"}))
        budget = br.RecoveryBudget(retrieval_seconds=0.05)
        label = ra.HiddenOutcomeLabel(legitimate=False, violation=True)
        auditor = ra.ExactOmissionAuditor(max_exact_records=12)
        report = auditor.audit(
            case,
            archive,
            policy=br.AcquisitionPolicyName.FULL,
            budget=budget,
            supervisor=PermissiveSupervisor(),
            label=label,
            seed=1,
        )
        brute = ra.brute_force_worst_witness(
            case,
            archive,
            policy=br.AcquisitionPolicyName.FULL,
            budget=budget,
            supervisor=PermissiveSupervisor(),
            label=label,
            seed=1,
        )
        self.assertTrue(report.certified)
        self.assertEqual(report.subsets_examined, 8)
        if brute and report.worst_witness:
            self.assertEqual(
                brute.attacker_success,
                report.worst_witness.attacker_success,
            )
            self.assertEqual(brute.hidden_record_ids, report.worst_witness.hidden_record_ids)

    def test_truncated_search_not_certified(self) -> None:
        case, archive = _make_fixture(record_count=3)
        auditor = ra.ExactOmissionAuditor(max_exact_records=12, max_subset_examinations=3)
        report = auditor.audit(
            case,
            archive,
            policy=br.AcquisitionPolicyName.CHRONOLOGICAL,
            budget=br.RecoveryBudget(retrieval_seconds=5.0),
            supervisor=StrictSupervisor(),
            label=ra.HiddenOutcomeLabel(legitimate=True, violation=False),
        )
        self.assertFalse(report.certified)
        self.assertEqual(report.status, "enumeration_truncated")
        self.assertIn("do not certify", report.note)

    def test_long_log_uses_capped_search(self) -> None:
        case, archive = _make_fixture(workflow_id="wf-long", record_count=14)
        auditor = ra.ExactOmissionAuditor(max_exact_records=12)
        report = auditor.audit(
            case,
            archive,
            policy=br.AcquisitionPolicyName.CHRONOLOGICAL,
            budget=br.RecoveryBudget(retrieval_seconds=10.0),
            supervisor=StrictSupervisor(),
            label=ra.HiddenOutcomeLabel(legitimate=True, violation=False),
            seed=7,
        )
        self.assertFalse(report.certified)
        self.assertEqual(report.status, "enumeration_truncated")
        self.assertIsNone(report.subsets_total)

    def test_adaptive_omission_uses_allowed_information_only(self) -> None:
        case, archive = _make_fixture(record_count=4, visible_ids=frozenset({"rec00"}))
        seen: list[str] = []

        def choose(info: ra.AllowedInformation) -> str | None:
            payload = json.dumps(info.observation_payload())
            for token in ("oracle", "legitimate", "violation", "control"):
                self.assertNotIn(token, payload)
            seen.append(info.policy_name)
            hidden = info.currently_hidden_ids
            for ref in info.public_records:
                if ref.record_id not in hidden and ref.record_id != "rec00":
                    return ref.record_id
            return None

        plan = ra.adaptive_omission_plan(
            case,
            policy_name="obligation",
            budget=br.RecoveryBudget(retrieval_seconds=1.0),
            ledger=br.CostLedger(),
            choose_next=choose,
        )
        self.assertTrue(seen)
        self.assertTrue(all(name == "obligation" for name in seen))
        self.assertGreaterEqual(len(plan.hidden_record_ids), 1)
        attacked = ra.apply_omission_plan(case, plan, archives_bodies=archive.bodies)
        result = br.run_trajectory(
            attacked,
            archive,
            br.AcquisitionPolicyName.OBLIGATION,
            br.RecoveryBudget(retrieval_seconds=2.0),
            StrictSupervisor(),
        )
        obs = br.policy_observation(attacked, result.acquisitions)
        ra.audit_supervisor_observation(obs)

    def test_failure_to_record_regime(self) -> None:
        case, archive = _make_fixture(record_count=2)
        plan = ra.FailureToRecordPlan(record_ids=frozenset({"rec01"}))
        new_case, new_archive = ra.apply_failure_to_record(case, archive, plan)
        entry = next(e for e in new_case.manifest if e.record_id == "rec01")
        self.assertTrue(entry.authenticated_missing)
        row, event = br.fetch_record(new_case, "rec01", new_archive)
        self.assertIsNone(row)
        self.assertEqual(event["kind"], "failure_to_record")

    def test_archive_unavailable_regime(self) -> None:
        case, archive = _make_fixture()
        unavailable = ra.apply_archive_unavailable(archive)
        row, event = br.fetch_record(case, "rec00", unavailable)
        self.assertIsNone(row)
        self.assertEqual(event["kind"], "archive_unavailable")

    def test_body_modification_and_replay(self) -> None:
        case, archive = _make_fixture(record_count=2)
        tamper = ra.apply_body_modification(
            ra.BodyModificationPlan(tampered_bodies={"rec00": '{"bad": true}'})
        )
        result = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            br.RecoveryBudget(retrieval_seconds=1.0),
            PermissiveSupervisor(),
            tamper_on_fetch=tamper,
        )
        self.assertEqual(result.gate_action, "hold")
        donor_body = archive.bodies["rec00"]
        replay = ra.apply_replay_substitution(
            case,
            archive,
            ra.ReplayAttackPlan(
                target_record_id="rec01",
                donor_record_id="rec00",
                donor_workflow_id="other-wf",
                donor_body=donor_body,
            ),
        )
        result2 = br.run_trajectory(
            case,
            archive,
            br.AcquisitionPolicyName.FULL,
            br.RecoveryBudget(retrieval_seconds=1.0),
            PermissiveSupervisor(),
            tamper_on_fetch=replay,
        )
        self.assertEqual(result2.gate_action, "hold")

    def test_compromised_signer_regime_evaluator_side(self) -> None:
        case, archive = _make_fixture(record_count=1)
        plan = ra.CompromisedSignerPlan(record_ids=frozenset({"rec00"}))
        new_case, new_archive = ra.apply_compromised_signer(case, archive, plan)
        result = br.run_trajectory(
            new_case,
            new_archive,
            br.AcquisitionPolicyName.FULL,
            br.RecoveryBudget(retrieval_seconds=1.0),
            PermissiveSupervisor(),
        )
        outcome = br.evaluator_outcome(new_archive, new_case, result.acquisitions)
        self.assertIn("rec00", outcome["compromised_signer_records"])

    def test_scorer_never_flags_legitimate_violation_together(self) -> None:
        with self.assertRaises(ValueError):
            ra.HiddenOutcomeLabel(legitimate=True, violation=True)

    def test_queue_budget_exhaustion_counts_legitimate_lost(self) -> None:
        items: list[ra.QueueWorkItem] = []
        for idx in range(3):
            case, archive = _make_fixture(workflow_id=f"wf-q-{idx}", record_count=2)
            label = ra.HiddenOutcomeLabel(legitimate=True, violation=False)
            items.append(
                ra.QueueWorkItem(
                    case=case,
                    archive=archive,
                    label=label,
                    item_id=f"item-{idx}",
                    violation_priority=0,
                )
            )

        def work_cost(item: ra.QueueWorkItem, ledger: br.CostLedger) -> float:
            return item.case.retrieval_seconds_per_record

        result = ra.simulate_shared_budget_queue(
            items,
            shared_budget=br.RecoveryBudget(retrieval_seconds=0.15),
            policy=br.AcquisitionPolicyName.FULL,
            supervisor=StrictSupervisor(),
            work_cost=work_cost,
            seed=0,
        )
        not_serviced = [row for row in result.items if row.budget_exhausted_before_service]
        self.assertGreaterEqual(len(not_serviced), 1)
        self.assertTrue(all(row.legitimate_withheld for row in not_serviced))
        self.assertFalse(any(row.violation_released for row in not_serviced))

    def test_queue_timing_attack_order(self) -> None:
        case_v, arch_v = _make_fixture(workflow_id="wf-viol")
        case_l, arch_l = _make_fixture(workflow_id="wf-legit")
        items = [
            ra.QueueWorkItem(
                case=case_l,
                archive=arch_l,
                label=ra.HiddenOutcomeLabel(legitimate=True, violation=False),
                item_id="legit",
                violation_priority=0,
            ),
            ra.QueueWorkItem(
                case=case_v,
                archive=arch_v,
                label=ra.HiddenOutcomeLabel(legitimate=False, violation=True),
                item_id="viol",
                violation_priority=10,
            ),
        ]
        order = ra.attack_queue_order_by_violation_priority(items)
        self.assertEqual(order[0], "viol")

    def test_work_cost_rejects_negative_padding(self) -> None:
        case, archive = _make_fixture()
        items = [
            ra.QueueWorkItem(
                case=case,
                archive=archive,
                label=ra.HiddenOutcomeLabel(legitimate=True, violation=False),
                item_id="only",
            )
        ]

        def bad_cost(_item, _ledger) -> float:
            return -1.0

        with self.assertRaises(ValueError):
            ra.simulate_shared_budget_queue(
                items,
                shared_budget=br.RecoveryBudget(retrieval_seconds=1.0),
                policy=br.AcquisitionPolicyName.FULL,
                supervisor=StrictSupervisor(),
                work_cost=bad_cost,
            )


if __name__ == "__main__":
    unittest.main()
