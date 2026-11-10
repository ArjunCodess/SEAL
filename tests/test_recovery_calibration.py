"""Offline tests for Learn then Test trajectory calibration."""
from __future__ import annotations

import hashlib
import unittest

from scipy.stats import beta as scipy_beta
from scipy.stats import binom

from src.recovery_calibration import (
    CALIBRATION_ASSUMPTIONS,
    CALIBRATION_CODE_DIGEST,
    StoppingPolicyCandidate,
    TrajectoryScore,
    clopper_pearson_upper_bound,
    exact_binomial_pmf,
    exact_binomial_tail_p_value,
    freeze_candidate_family_digest,
    learn_then_test_select,
    holdout_calibration_audit,
    threshold_release_decision,
    unsafe_trajectory_loss,
)


def _scipy_lower_tail(*, successes: int, n: int, null_p: float) -> float:
    return float(binom.cdf(successes, n, null_p))


def _scipy_cp_upper(*, successes: int, n: int, delta: float) -> float:
    if successes == n:
        return 1.0
    return float(scipy_beta.ppf(1.0 - delta, successes + 1, n - successes))


class RecoveryCalibrationTests(unittest.TestCase):
    def test_unsafe_trajectory_loss(self) -> None:
        self.assertEqual(unsafe_trajectory_loss(violation=True, released=True), 1)
        self.assertEqual(unsafe_trajectory_loss(violation=True, released=False), 0)
        self.assertEqual(unsafe_trajectory_loss(violation=False, released=True), 0)

    def test_threshold_release_is_strictly_below_danger(self) -> None:
        self.assertTrue(threshold_release_decision(0.3, 0.5))
        self.assertFalse(threshold_release_decision(0.7, 0.5))
        self.assertFalse(threshold_release_decision(0.5, 0.5))

    def test_lower_tail_matches_scipy(self) -> None:
        for n, s, p in ((0, 0, 0.05), (1, 0, 0.1), (1, 1, 0.1), (10, 3, 0.2), (50, 0, 0.05)):
            got = exact_binomial_tail_p_value(successes=s, n=n, null_p=p)
            if n == 0:
                self.assertEqual(got, 1.0)
                continue
            exp = _scipy_lower_tail(successes=s, n=n, null_p=p)
            self.assertAlmostEqual(got, exp, places=12)

    def test_lower_tail_zero_unsafe_not_degenerate_one(self) -> None:
        p0 = exact_binomial_tail_p_value(successes=0, n=20, null_p=0.05)
        self.assertLess(p0, 0.5)
        self.assertGreater(p0, 0.0)

    def test_all_unsafe_never_passes_at_target_05(self) -> None:
        tail = exact_binomial_tail_p_value(successes=10, n=10, null_p=0.05)
        self.assertAlmostEqual(tail, 1.0)
        bonf = min(1.0, tail * 3)
        self.assertFalse(bonf <= 0.05)

    def test_clopper_pearson_scipy_crosscheck(self) -> None:
        delta = 0.05
        cases = ((0, 10), (1, 10), (10, 10), (0, 1), (0, 200), (7, 200))
        for s, n in cases:
            if n == 0:
                with self.assertRaises(ValueError):
                    clopper_pearson_upper_bound(successes=s, n=n, delta=delta)
                continue
            got = clopper_pearson_upper_bound(successes=s, n=n, delta=delta)
            exp = _scipy_cp_upper(successes=s, n=n, delta=delta)
            self.assertAlmostEqual(got, exp, places=10)
            self.assertGreaterEqual(got, s / n if n else 0.0)

    def test_clopper_pearson_rejects_n_zero(self) -> None:
        with self.assertRaises(ValueError):
            clopper_pearson_upper_bound(successes=0, n=0, delta=0.05)

    def test_binomial_pmf_matches_toy_example(self) -> None:
        self.assertAlmostEqual(exact_binomial_pmf(2, 1, 0.5), 0.5)

    def test_family_digest_depends_on_full_policy_id_and_assumptions(self) -> None:
        short = StoppingPolicyCandidate(policy_id="policy-alpha", release_threshold=0.5)
        long = StoppingPolicyCandidate(policy_id="policy-alpha-extended-stop-v3", release_threshold=0.5)
        d_short = freeze_candidate_family_digest(
            [short], target_risk=0.05, family_error=0.1
        )
        d_long = freeze_candidate_family_digest(
            [long], target_risk=0.05, family_error=0.1
        )
        self.assertNotEqual(d_short, d_long)
        d_delta = freeze_candidate_family_digest(
            [short], target_risk=0.05, family_error=0.2
        )
        self.assertNotEqual(d_short, d_delta)
        self.assertEqual(len(CALIBRATION_CODE_DIGEST), 64)
        self.assertNotEqual(
            hashlib.sha256(b"policy-alpha").hexdigest()[: len(d_short)],
            d_short,
        )

    def test_duplicate_policy_id_in_family_rejected(self) -> None:
        c = StoppingPolicyCandidate(policy_id="same", release_threshold=0.5)
        with self.assertRaises(ValueError):
            freeze_candidate_family_digest([c, c], target_risk=0.05, family_error=0.1)

    def test_learn_rejects_zero_rows(self) -> None:
        c = StoppingPolicyCandidate(policy_id="p1", release_threshold=0.5)
        with self.assertRaises(ValueError):
            learn_then_test_select([c], [], target_risk=0.05, family_error=0.1)

    def test_policy_mismatch_and_duplicate_group_rejected(self) -> None:
        c = StoppingPolicyCandidate(policy_id="stop/policy-a", release_threshold=0.8)
        rows = (
            TrajectoryScore(
                trajectory_id="t1",
                partition_group="g1",
                risk_score=0.1,
                violation=False,
                stopping_policy_id="stop/other",
            ),
        )
        with self.assertRaises(ValueError):
            learn_then_test_select([c], rows, target_risk=0.2, family_error=0.1)
        rows_dup = (
            TrajectoryScore("t1", "g1", 0.1, False, "stop/policy-a"),
            TrajectoryScore("t2", "g1", 0.2, False, "stop/policy-a"),
        )
        with self.assertRaises(ValueError):
            learn_then_test_select([c], rows_dup, target_risk=0.2, family_error=0.1)

    def test_large_zero_unsafe_can_pass(self) -> None:
        pid = "stop/release/threshold=0.99/whole_trajectory_v1"
        c = StoppingPolicyCandidate(policy_id=pid, release_threshold=0.99)
        rows = tuple(
            TrajectoryScore(
                trajectory_id=f"learn-{i}",
                partition_group=f"g{i}",
                risk_score=0.5,
                violation=False,
                stopping_policy_id=pid,
            )
            for i in range(80)
        )
        learn = learn_then_test_select([c], rows, target_risk=0.05, family_error=0.1)
        self.assertIsNotNone(learn.selected)
        self.assertEqual(learn.learn_unsafe_count, 0)
        self.assertLessEqual(learn.learn_upper_bound, 0.05 + 1e-9)

    def test_no_passing_candidate_selected_none(self) -> None:
        pid = "stop/strict"
        c = StoppingPolicyCandidate(policy_id=pid, release_threshold=0.01)
        rows = tuple(
            TrajectoryScore(
                trajectory_id=f"t{i}",
                partition_group=f"g{i}",
                risk_score=0.9,
                violation=True,
                stopping_policy_id=pid,
            )
            for i in range(30)
        )
        learn = learn_then_test_select([c], rows, target_risk=0.05, family_error=0.05)
        self.assertIsNone(learn.selected)

    def test_learn_then_test_select_and_audit_disjoint_holdout(self) -> None:
        policies = (
            "stop/release/threshold=0.90/whole_trajectory_v1",
            "stop/release/threshold=0.50/whole_trajectory_v1",
            "stop/release/threshold=0.10/whole_trajectory_v1",
        )
        candidates = tuple(
            StoppingPolicyCandidate(policy_id=p, release_threshold=float(p.split("=")[1].split("/")[0]))
            for p in policies
        )
        learn_rows: list[TrajectoryScore] = []
        for i in range(24):
            for p, thr in zip(policies, (0.90, 0.50, 0.10)):
                learn_rows.append(
                    TrajectoryScore(
                        trajectory_id=f"learn-{p}-{i}",
                        partition_group=f"g-{p}-{i}",
                        risk_score=0.2 if i % 4 else 0.95,
                        violation=bool(i % 4 == 0),
                        stopping_policy_id=p,
                    )
                )
        test_rows = tuple(
            TrajectoryScore(
                trajectory_id=f"test-{i}",
                partition_group=f"h{i}",
                risk_score=0.15,
                violation=False,
                stopping_policy_id=policies[0],
            )
            for i in range(20)
        )
        target_risk = 0.2
        family_error = 0.2
        learn = learn_then_test_select(
            candidates, learn_rows, target_risk=target_risk, family_error=family_error
        )
        self.assertIn("i.i.d.", learn.assumptions[0])
        learn_ids = frozenset(r.trajectory_id for r in learn_rows)
        if learn.selected is not None:
            audit = holdout_calibration_audit(
                learn.selected,
                candidates,
                test_rows,
                target_risk=target_risk,
                family_error=family_error,
                learn_trajectory_ids=learn_ids,
                learn_partition_groups=frozenset({"learn-group"}),
            )
            self.assertEqual(audit.family_digest, learn.family_digest)
            self.assertEqual(audit.multiplicity_candidates, len(candidates))
            self.assertEqual(audit.shift_guarantee, "none")
            self.assertGreaterEqual(audit.clopper_pearson_upper_bound, audit.test_empirical_risk)
        with self.assertRaises(ValueError):
            holdout_calibration_audit(
                learn.selected,
                candidates,
                test_rows,
                target_risk=target_risk,
                family_error=family_error,
                learn_trajectory_ids=frozenset({"test-0"}),
                learn_partition_groups=frozenset({"learn-group"}),
            )

    def test_audit_none_selected(self) -> None:
        c = StoppingPolicyCandidate(policy_id="p", release_threshold=0.5)
        test_rows = (
            TrajectoryScore("t1", "g1", 0.1, False, "p"),
        )
        audit = holdout_calibration_audit(
            None,
            [c],
            test_rows,
            target_risk=0.05,
            family_error=0.1,
            learn_trajectory_ids=frozenset({"learn-only"}),
            learn_partition_groups=frozenset({"learn-group"}),
        )
        self.assertFalse(audit.passes_bonferroni)
        self.assertIsNone(audit.selected_policy_id)

    def test_assumptions_document_no_shift_guarantee(self) -> None:
        self.assertTrue(len(CALIBRATION_ASSUMPTIONS) >= 3)
        joined = " ".join(CALIBRATION_ASSUMPTIONS)
        self.assertIn("shift", joined.lower())


if __name__ == "__main__":
    unittest.main()
