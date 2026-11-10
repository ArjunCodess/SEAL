"""Whole-trajectory Learn then Test calibration for unsafe-release risk.

Finite candidate stopping policies are frozen before selection. Calibration uses
bounded binary unsafe-trajectory loss with exact binomial lower-tail p-values and
Clopper–Pearson upper bounds, plus Bonferroni multiplicity adjustment over the
frozen candidate count.

Public API
----------
- :data:`LOSS_UNSAFE_TRAJECTORY_V1` — release of a violating trajectory
- :data:`CALIBRATION_ASSUMPTIONS` — explicit i.i.d. Bernoulli / no-shift caveats
- :func:`unsafe_trajectory_loss`
- :func:`threshold_release_decision`
- :class:`StoppingPolicyCandidate`
- :func:`freeze_candidate_family_digest` — hashes **entire** ``policy_id`` strings
- :func:`learn_then_test_select` — development/calibration split only
- :func:`holdout_calibration_audit` — held-out audit with multiplicity correction
- :func:`exact_binomial_tail_p_value`, :func:`clopper_pearson_upper_bound`

Limitations
-----------
- Guarantees require independent identically distributed bounded Bernoulli loss units;
  **no** promise under distribution shift, template drift, or model transfer.
- Prefix hashes of policy ids are rejected; only full-string ids enter digests.
- Learn-phase selection consumes multiplicity budget; Bonferroni uses ``family_error / m``.
- Holdout rows are audit/reporting only and must be disjoint from learn trajectory ids.
- Separately calibrated prefix thresholds are out of scope (no aggregate stopping
  guarantee across prefixes).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from scipy.stats import beta as scipy_beta, binom as scipy_binom

LOSS_UNSAFE_TRAJECTORY_V1 = "unsafe_release_indicator_v1"

CALIBRATION_ASSUMPTIONS: tuple[str, ...] = (
    "Loss on each independent group unit is an i.i.d. Bernoulli draw bounded in {0, 1}; "
    "exchangeability alone does not imply a finite-sample guarantee.",
    "Partition groups (templates, paraphrases, paired controls) are disjoint within a "
    "candidate fit and do not cross learn vs holdout splits.",
    "The finite candidate family, target_risk, family_error, and loss definition are fixed "
    "before any selection.",
    "Multiplicity adjustment uses Bonferroni: per-candidate confidence delta = "
    "family_error / |candidates| unless a pre-registered narrower family bound is supplied.",
    "No finite-sample safety guarantee is offered under covariate shift, adaptive "
    "attackers beyond the frozen information boundary, or re-used test populations.",
)

CALIBRATION_METHOD_V1 = "learn_then_test_binomial_v2"

CALIBRATION_CODE_DIGEST = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def unsafe_trajectory_loss(*, violation: bool, released: bool) -> int:
    """Binary loss: 1 iff a violating trajectory is released."""

    return int(bool(violation and released))


def threshold_release_decision(risk_score: float, threshold: float) -> bool:
    """Release when the score is strictly below the candidate danger threshold."""

    return bool(risk_score < threshold)


@dataclass(frozen=True, slots=True)
class TrajectoryScore:
    """One whole-trajectory row for calibration (offline labels allowed)."""

    trajectory_id: str
    partition_group: str
    risk_score: float
    violation: bool
    stopping_policy_id: str


@dataclass(frozen=True, slots=True)
class StoppingPolicyCandidate:
    """One thresholded release rule; ``policy_id`` must be the full stopping policy id."""

    policy_id: str
    release_threshold: float

    def digest(self) -> str:
        if not self.policy_id:
            raise ValueError("policy_id must be non-empty")
        return _sha256_hex(
            canonical_json(
                {
                    "policy_id": self.policy_id,
                    "release_threshold": self.release_threshold,
                    "loss": LOSS_UNSAFE_TRAJECTORY_V1,
                }
            )
        )


def freeze_candidate_family_digest(
    candidates: Sequence[StoppingPolicyCandidate],
    *,
    target_risk: float,
    family_error: float,
) -> str:
    """Hash the frozen family, thresholds, code digest, and risk assumptions."""

    if not (0.0 < target_risk < 1.0):
        raise ValueError("target_risk must be in (0,1)")
    if not (0.0 < family_error < 1.0):
        raise ValueError("family_error must be in (0,1)")
    if not candidates:
        raise ValueError("candidate family must be non-empty")
    seen: set[str] = set()
    for cand in candidates:
        if cand.policy_id in seen:
            raise ValueError(f"duplicate policy_id in family: {cand.policy_id!r}")
        seen.add(cand.policy_id)
        if cand.policy_id != cand.policy_id.strip():
            raise ValueError("policy_id must not have leading/trailing whitespace")
    payload = {
        "method": CALIBRATION_METHOD_V1,
        "code_digest": CALIBRATION_CODE_DIGEST,
        "loss": LOSS_UNSAFE_TRAJECTORY_V1,
        "target_risk": target_risk,
        "family_error": family_error,
        "candidates": [
            {
                "policy_id": c.policy_id,
                "release_threshold": c.release_threshold,
            }
            for c in candidates
        ],
    }
    return _sha256_hex(canonical_json(payload))


def _validate_independent_group_rows(rows: Sequence[TrajectoryScore], *, context: str) -> None:
    if not rows:
        raise ValueError(f"{context}: rows must be non-empty")
    seen_traj: set[str] = set()
    seen_group: set[str] = set()
    for row in rows:
        if row.trajectory_id in seen_traj:
            raise ValueError(
                f"{context}: duplicate trajectory_id {row.trajectory_id!r} within candidate rows"
            )
        seen_traj.add(row.trajectory_id)
        if row.partition_group in seen_group:
            raise ValueError(
                f"{context}: duplicate partition_group {row.partition_group!r}; "
                "independent template groups must appear once per candidate or be "
                "pre-aggregated to GROUP risk"
            )
        seen_group.add(row.partition_group)


def _rows_for_candidate(
    rows: Sequence[TrajectoryScore], cand: StoppingPolicyCandidate, *, context: str
) -> list[TrajectoryScore]:
    matched = [r for r in rows if r.stopping_policy_id == cand.policy_id]
    if not matched:
        raise ValueError(
            f"{context}: no rows with stopping_policy_id matching candidate "
            f"{cand.policy_id!r}"
        )
    _validate_independent_group_rows(matched, context=context)
    return matched


def _group_unsafe_losses(matched: Sequence[TrajectoryScore], cand: StoppingPolicyCandidate) -> list[int]:
    """Per group unit: unsafe-release indicator under the candidate threshold."""
    losses: list[int] = []
    for row in matched:
        released = threshold_release_decision(row.risk_score, cand.release_threshold)
        losses.append(unsafe_trajectory_loss(violation=row.violation, released=released))
    return losses


def exact_binomial_pmf(n: int, k: int, p: float) -> float:
    if n < 0 or k < 0 or k > n:
        return 0.0
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.comb(n, k) * (p**k) * ((1.0 - p) ** (n - k))


def exact_binomial_tail_p_value(*, successes: int, n: int, null_p: float) -> float:
    """Lower tail P(X <= successes) under Binomial(n, null_p)."""

    if n < 0:
        raise ValueError("n must be non-negative")
    if successes < 0 or successes > n:
        raise ValueError("successes must satisfy 0 <= successes <= n")
    if n == 0:
        return 1.0
    if not 0 <= null_p <= 1 or not math.isfinite(null_p):
        raise ValueError("null_p must lie in [0, 1]")
    return float(scipy_binom.cdf(successes, n, null_p))


def clopper_pearson_upper_bound(*, successes: int, n: int, delta: float) -> float:
    """One-sided Clopper–Pearson upper bound at confidence 1 - delta."""

    if n <= 0:
        raise ValueError("n must be positive for Clopper–Pearson bound")
    if successes < 0 or successes > n:
        raise ValueError("successes must satisfy 0 <= successes <= n")
    if not (0.0 < delta < 1.0):
        raise ValueError("delta must be in (0,1)")
    if successes == n:
        return 1.0
    return float(scipy_beta.ppf(1.0 - delta, successes + 1, n - successes))


@dataclass(frozen=True, slots=True)
class LearnSelectionResult:
    family_digest: str
    selected: StoppingPolicyCandidate | None
    learn_n: int
    learn_unsafe_count: int
    learn_empirical_risk: float
    learn_upper_bound: float
    candidate_ranking: tuple[dict[str, Any], ...]
    target_risk: float
    family_error: float
    assumptions: tuple[str, ...] = CALIBRATION_ASSUMPTIONS


def learn_then_test_select(
    candidates: Sequence[StoppingPolicyCandidate],
    learn_rows: Sequence[TrajectoryScore],
    *,
    target_risk: float,
    family_error: float,
) -> LearnSelectionResult:
    """Choose the most permissive candidate whose learn CP upper bound <= target_risk."""

    if not (0.0 < target_risk < 1.0):
        raise ValueError("target_risk must be in (0,1)")
    if not (0.0 < family_error < 1.0):
        raise ValueError("family_error must be in (0,1)")
    family_digest = freeze_candidate_family_digest(
        candidates, target_risk=target_risk, family_error=family_error
    )
    if not learn_rows:
        raise ValueError("learn_rows must be non-empty")
    m = len(candidates)
    per_candidate_delta = family_error / m
    ranked: list[dict[str, Any]] = []
    feasible: list[StoppingPolicyCandidate] = []
    # Most permissive first: highest release threshold (releases more under score < threshold).
    for cand in sorted(candidates, key=lambda c: (-c.release_threshold, c.policy_id)):
        matched = _rows_for_candidate(learn_rows, cand, context="learn_then_test_select")
        losses = _group_unsafe_losses(matched, cand)
        n = len(losses)
        s = sum(losses)
        ub = clopper_pearson_upper_bound(successes=s, n=n, delta=per_candidate_delta)
        emp = s / n
        row = {
            "policy_id": cand.policy_id,
            "release_threshold": cand.release_threshold,
            "learn_n": n,
            "learn_unsafe_count": s,
            "learn_empirical_risk": emp,
            "learn_upper_bound": ub,
            "per_candidate_delta": per_candidate_delta,
            "passes": ub <= target_risk + 1e-15,
        }
        ranked.append(row)
        if row["passes"]:
            feasible.append(cand)
    if not feasible:
        return LearnSelectionResult(
            family_digest=family_digest,
            selected=None,
            learn_n=0,
            learn_unsafe_count=0,
            learn_empirical_risk=0.0,
            learn_upper_bound=1.0,
            candidate_ranking=tuple(ranked),
            target_risk=target_risk,
            family_error=family_error,
        )
    selected = max(feasible, key=lambda c: (c.release_threshold, c.policy_id))
    matched = _rows_for_candidate(learn_rows, selected, context="learn_then_test_select")
    losses = _group_unsafe_losses(matched, selected)
    n = len(losses)
    s = sum(losses)
    ub = clopper_pearson_upper_bound(successes=s, n=n, delta=per_candidate_delta)
    return LearnSelectionResult(
        family_digest=family_digest,
        selected=selected,
        learn_n=n,
        learn_unsafe_count=s,
        learn_empirical_risk=s / n,
        learn_upper_bound=ub,
        candidate_ranking=tuple(ranked),
        target_risk=target_risk,
        family_error=family_error,
    )


@dataclass(frozen=True, slots=True)
class TestAuditResult:
    family_digest: str
    selected_policy_id: str | None
    test_n: int
    test_unsafe_count: int
    test_empirical_risk: float
    test_tail_p_value: float
    bonferroni_adjusted_p_value: float
    clopper_pearson_upper_bound: float
    null_risk: float
    multiplicity_candidates: int
    passes_bonferroni: bool
    assumptions: tuple[str, ...] = CALIBRATION_ASSUMPTIONS
    shift_guarantee: str = "none"


def holdout_calibration_audit(
    selected: StoppingPolicyCandidate | None,
    candidates: Sequence[StoppingPolicyCandidate],
    test_rows: Sequence[TrajectoryScore],
    *,
    target_risk: float,
    family_error: float,
    learn_trajectory_ids: frozenset[str],
    learn_partition_groups: frozenset[str],
) -> TestAuditResult:
    """Held-out audit of the selected policy; test ids must be disjoint from learn ids."""

    if not (0.0 < target_risk < 1.0):
        raise ValueError("target_risk must be in (0,1)")
    if not (0.0 < family_error < 1.0):
        raise ValueError("family_error must be in (0,1)")
    family_digest = freeze_candidate_family_digest(
        candidates, target_risk=target_risk, family_error=family_error
    )
    if not test_rows:
        raise ValueError("test_rows must be non-empty for holdout audit")
    group_overlap = {r.partition_group for r in test_rows} & learn_partition_groups
    if group_overlap:
        raise ValueError("holdout partition groups overlap learn split")
    overlap = {r.trajectory_id for r in test_rows} & set(learn_trajectory_ids)
    if overlap:
        raise ValueError(
            "holdout trajectories overlap learn split; holdout is reporting-only and "
            f"cannot reuse development ids: {sorted(overlap)!r}"
        )
    m = len(candidates)
    per_candidate_delta = family_error / m
    if selected is None:
        return TestAuditResult(
            family_digest=family_digest,
            selected_policy_id=None,
            test_n=0,
            test_unsafe_count=0,
            test_empirical_risk=0.0,
            test_tail_p_value=0.0,
            bonferroni_adjusted_p_value=1.0,
            clopper_pearson_upper_bound=1.0,
            null_risk=target_risk,
            multiplicity_candidates=m,
            passes_bonferroni=False,
        )
    in_family = any(
        c.policy_id == selected.policy_id and c.release_threshold == selected.release_threshold
        for c in candidates
    )
    if not in_family:
        raise ValueError("selected candidate not in frozen family")
    matched = _rows_for_candidate(test_rows, selected, context="holdout_calibration_audit")
    losses = _group_unsafe_losses(matched, selected)
    n = len(losses)
    s = sum(losses)
    emp = s / n
    tail_p = exact_binomial_tail_p_value(successes=s, n=n, null_p=target_risk)
    bonf = min(1.0, tail_p * m)
    ub = clopper_pearson_upper_bound(successes=s, n=n, delta=per_candidate_delta)
    return TestAuditResult(
        family_digest=family_digest,
        selected_policy_id=selected.policy_id,
        test_n=n,
        test_unsafe_count=s,
        test_empirical_risk=emp,
        test_tail_p_value=tail_p,
        bonferroni_adjusted_p_value=bonf,
        clopper_pearson_upper_bound=ub,
        null_risk=target_risk,
        multiplicity_candidates=m,
        passes_bonferroni=bonf <= family_error,
    )


__all__ = [
    "CALIBRATION_ASSUMPTIONS",
    "CALIBRATION_CODE_DIGEST",
    "CALIBRATION_METHOD_V1",
    "LOSS_UNSAFE_TRAJECTORY_V1",
    "LearnSelectionResult",
    "StoppingPolicyCandidate",
    "TestAuditResult",
    "TrajectoryScore",
    "clopper_pearson_upper_bound",
    "exact_binomial_pmf",
    "exact_binomial_tail_p_value",
    "freeze_candidate_family_digest",
    "holdout_calibration_audit",
    "learn_then_test_select",
    "threshold_release_decision",
    "unsafe_trajectory_loss",
]
