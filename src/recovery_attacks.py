"""Standalone attack plans, exact auditors, and queue budget simulators.

Implements attack coverage described in ``docs/budgeted-recovery-protocol.md`` and
``docs/recovery-engineering-review.md``. This module is independent of the paid
development pilot and performs no network I/O.

Public API
----------
- :class:`AttackRegime` — attack-side labelling aligned with trust-table regimes.
- :class:`PublicRecordRef` — manifest-visible metadata (no bodies).
- :class:`OmissionAttackPlan`, :func:`apply_omission_plan`, :func:`plan_random_omission`
- :class:`AllowedInformation` — frozen information boundary for adaptive attacks.
- :func:`adaptive_omission_plan` — policy-aware omission via an explicit callback.
- Regime applicators: :func:`apply_failure_to_record`, :func:`apply_archive_unavailable`,
  :func:`apply_body_modification`, :func:`apply_replay_substitution`,
  :func:`apply_compromised_signer`
- :class:`HiddenOutcomeLabel`, :func:`score_trajectory_outcome` — scorer-only oracle.
- :class:`ExactOmissionAuditor`, :func:`audit_omission_worst_case`
- :class:`QueueWorkItem`, :func:`simulate_shared_budget_queue`
"""
from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from src import budgeted_recovery as br

# ---------------------------------------------------------------------------
# Regimes and public metadata
# ---------------------------------------------------------------------------


class AttackRegime(str, Enum):
    """Distinct attack families; maps to archive outcomes under replay."""

    OMISSION_AFTER_RECORDING = "omission_after_recording"
    FAILURE_TO_RECORD = "failure_to_record"
    ARCHIVE_UNAVAILABLE = "archive_unavailable"
    BODY_MODIFICATION = "body_modification"
    REPLAY_WRONG_CONTEXT = "replay_wrong_context"
    COMPROMISED_SIGNER = "compromised_signer"
    RANDOM_OUTAGE = "random_outage"
    QUEUE_BUDGET_TIMING = "queue_budget_timing"


@dataclass(frozen=True, slots=True)
class PublicRecordRef:
    """Policy-visible manifest row without body content."""

    record_id: str
    kind: str
    digest: br.BodyDigest
    bytes: int
    authenticated_missing: bool

    @classmethod
    def from_entry(cls, entry: br.ManifestEntry) -> PublicRecordRef:
        return cls(
            record_id=entry.record_id,
            kind=entry.kind,
            digest=entry.digest,
            bytes=entry.bytes,
            authenticated_missing=entry.authenticated_missing,
        )


def public_manifest(case: br.EvidenceCase) -> tuple[PublicRecordRef, ...]:
    return tuple(PublicRecordRef.from_entry(e) for e in case.manifest)


# ---------------------------------------------------------------------------
# Omission attack plans (recorded bodies withheld from initial view)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OmissionAttackPlan:
    """Withhold listed bodies from the worker-visible view; manifest metadata stays."""

    regime: AttackRegime = AttackRegime.OMISSION_AFTER_RECORDING
    hidden_record_ids: frozenset[str] = frozenset()
    plan_id: str = "omission-v1"

    def validate_against(self, case: br.EvidenceCase) -> None:
        ids = {e.record_id for e in case.manifest}
        unknown = self.hidden_record_ids - ids
        if unknown:
            raise ValueError(f"omission plan references unknown record ids: {sorted(unknown)}")
        for entry in case.manifest:
            if entry.record_id in self.hidden_record_ids and entry.authenticated_missing:
                raise ValueError(
                    f"cannot omit failure-to-record row {entry.record_id}; use FailureToRecordPlan"
                )


def eligible_omission_ids(case: br.EvidenceCase) -> frozenset[str]:
    """Record ids that may be withheld while an authentic archive body still exists."""

    return frozenset(
        e.record_id for e in case.manifest if not e.authenticated_missing
    )


def apply_omission_plan(
    case: br.EvidenceCase,
    plan: OmissionAttackPlan,
    *,
    archives_bodies: Mapping[str, str],
) -> br.EvidenceCase:
    """Return a case whose initial view hides ``plan.hidden_record_ids`` bodies."""

    plan.validate_against(case)
    _ = archives_bodies  # bodies stay in archive; attack only changes the initial worker view
    new_visible = {
        rid: body
        for rid, body in case.initial_visible_bodies.items()
        if rid not in plan.hidden_record_ids
    }
    return replace(case, initial_visible_bodies=new_visible)


def plan_random_omission(
    case: br.EvidenceCase,
    *,
    seed: int,
    hide_fraction: float = 0.5,
    plan_id: str | None = None,
) -> OmissionAttackPlan:
    """Seeded random omission over eligible manifest ids."""

    if not 0.0 <= hide_fraction <= 1.0:
        raise ValueError("hide_fraction must be in [0, 1]")
    eligible = sorted(eligible_omission_ids(case))
    if not eligible:
        return OmissionAttackPlan(hidden_record_ids=frozenset(), plan_id=plan_id or f"random-{seed}")
    rng = random.Random(f"omission:{seed}:{case.workflow_id}")
    hide: set[str] = set()
    for record_id in eligible:
        if rng.random() < hide_fraction:
            hide.add(record_id)
    return OmissionAttackPlan(
        hidden_record_ids=frozenset(hide),
        regime=AttackRegime.RANDOM_OUTAGE,
        plan_id=plan_id or f"random-{seed}",
    )


# ---------------------------------------------------------------------------
# Other trust-table regimes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FailureToRecordPlan:
    record_ids: frozenset[str]
    plan_id: str = "failure-to-record-v1"

    def regime(self) -> AttackRegime:
        return AttackRegime.FAILURE_TO_RECORD


def apply_failure_to_record(
    case: br.EvidenceCase,
    archive: br.TrustedArchive,
    plan: FailureToRecordPlan,
    *,
    signing_key: Any = None,
) -> tuple[br.EvidenceCase, br.TrustedArchive]:
    """Mark rows as authenticated-missing and drop bodies from the protected archive."""

    missing = set(plan.record_ids)
    records: list[tuple[str, str, str]] = []
    for entry in case.manifest:
        body = archive.bodies.get(entry.record_id, "")
        records.append((entry.record_id, entry.kind, body))
    manifest, key = br.build_manifest(
        case.workflow_id,
        records,
        signing_key=signing_key,
        missing_ids=frozenset(missing),
    )
    bodies = {
        rid: body
        for rid, _k, body in records
        if rid not in missing
    }
    new_archive = br.TrustedArchive.build(case.workflow_id, manifest, bodies, signing_key=key)
    new_case = replace(case, manifest=manifest, initial_visible_bodies={})
    return new_case, new_archive


@dataclass(frozen=True, slots=True)
class ArchiveUnavailablePlan:
    plan_id: str = "archive-unavailable-v1"

    def regime(self) -> AttackRegime:
        return AttackRegime.ARCHIVE_UNAVAILABLE


def apply_archive_unavailable(
    archive: br.TrustedArchive,
    _plan: ArchiveUnavailablePlan | None = None,
) -> br.TrustedArchive:
    return br.TrustedArchive(
        public_key_b64=archive.public_key_b64,
        bodies=archive.bodies,
        signatures=archive.signatures,
        available=False,
    )


@dataclass(frozen=True, slots=True)
class BodyModificationPlan:
    tampered_bodies: Mapping[str, str]
    plan_id: str = "body-modification-v1"

    def regime(self) -> AttackRegime:
        return AttackRegime.BODY_MODIFICATION


def apply_body_modification(plan: BodyModificationPlan) -> Mapping[str, str]:
    return dict(plan.tampered_bodies)


@dataclass(frozen=True, slots=True)
class ReplayAttackPlan:
    """Replay an authentic body under the wrong record id (wrong workflow context)."""

    target_record_id: str
    donor_record_id: str
    donor_workflow_id: str
    donor_body: str
    plan_id: str = "replay-v1"

    def regime(self) -> AttackRegime:
        return AttackRegime.REPLAY_WRONG_CONTEXT


def apply_replay_substitution(
    case: br.EvidenceCase,
    archive: br.TrustedArchive,
    plan: ReplayAttackPlan,
) -> Mapping[str, str]:
    if plan.target_record_id not in {e.record_id for e in case.manifest}:
        raise ValueError("replay target not in manifest")
    tamper = {plan.target_record_id: plan.donor_body}
    return tamper


@dataclass(frozen=True, slots=True)
class CompromisedSignerPlan:
    record_ids: frozenset[str]
    plan_id: str = "compromised-signer-v1"

    def regime(self) -> AttackRegime:
        return AttackRegime.COMPROMISED_SIGNER


def apply_compromised_signer(
    case: br.EvidenceCase,
    archive: br.TrustedArchive,
    plan: CompromisedSignerPlan,
    *,
    signing_key: Any = None,
) -> tuple[br.EvidenceCase, br.TrustedArchive]:
    """Mark compromised rows on the archive for evaluator-side scoring only."""

    _ = signing_key
    new_archive = br.TrustedArchive(
        public_key_b64=archive.public_key_b64,
        bodies=archive.bodies,
        signatures=archive.signatures,
        available=archive.available,
        compromised_record_ids=frozenset(plan.record_ids),
    )
    return case, new_archive


# ---------------------------------------------------------------------------
# Adaptive omission (explicit allowed-information callback)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllowedInformation:
    """Frozen attacker-visible state; must not include oracle / control fields."""

    workflow_id: str
    task: str
    proposal: str
    worker_claims: tuple[str, ...]
    public_records: tuple[PublicRecordRef, ...]
    initially_visible_ids: frozenset[str]
    currently_hidden_ids: frozenset[str]
    policy_name: str
    retrieval_budget_remaining: float
    acquisition_count: int

    def observation_payload(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "task": self.task,
            "proposal": self.proposal,
            "worker_claims": list(self.worker_claims),
            "public_records": [
                {
                    "record_id": ref.record_id,
                    "kind": ref.kind,
                    "digest": ref.digest,
                    "bytes": ref.bytes,
                    "authenticated_missing": ref.authenticated_missing,
                }
                for ref in self.public_records
            ],
            "initially_visible_ids": sorted(self.initially_visible_ids),
            "currently_hidden_ids": sorted(self.currently_hidden_ids),
            "policy_name": self.policy_name,
            "retrieval_budget_remaining": self.retrieval_budget_remaining,
            "acquisition_count": self.acquisition_count,
        }


AllowedInformationCallback = Callable[[AllowedInformation], str | None]
"""Return the next record id to hide, or ``None`` to stop extending the omission set."""


def _audit_allowed_information(info: AllowedInformation) -> None:
    blob = br.canonical_json(info.observation_payload())
    for token in br._FORBIDDEN_FEATURE_TOKENS:
        if token in blob:
            raise ValueError(f"forbidden token {token!r} in allowed-information payload")


def adaptive_omission_plan(
    case: br.EvidenceCase,
    *,
    policy_name: str,
    budget: br.RecoveryBudget,
    ledger: br.CostLedger,
    choose_next: AllowedInformationCallback,
    max_steps: int = 64,
) -> OmissionAttackPlan:
    """Build an omission plan stepwise; ``choose_next`` sees only :class:`AllowedInformation`."""

    hidden: set[str] = set()
    visible_ids = frozenset(case.initial_visible_bodies)
    eligible = eligible_omission_ids(case)
    for step in range(max_steps):
        remaining = budget.retrieval_seconds - ledger.retrieval_seconds
        info = AllowedInformation(
            workflow_id=case.workflow_id,
            task=case.task,
            proposal=case.proposal,
            worker_claims=case.worker_claims,
            public_records=public_manifest(case),
            initially_visible_ids=visible_ids,
            currently_hidden_ids=frozenset(hidden),
            policy_name=policy_name,
            retrieval_budget_remaining=remaining,
            acquisition_count=step,
        )
        _audit_allowed_information(info)
        nxt = choose_next(info)
        if nxt is None:
            break
        if nxt not in eligible:
            raise ValueError(f"callback chose ineligible record id {nxt!r}")
        if nxt in hidden:
            break
        hidden.add(nxt)
    return OmissionAttackPlan(
        hidden_record_ids=frozenset(hidden),
        plan_id=f"adaptive-{policy_name}",
    )


# ---------------------------------------------------------------------------
# Scorer-only hidden labels (never passed to policies)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HiddenOutcomeLabel:
    """Known only to the attack scorer, not to acquisition policies or supervisors."""

    legitimate: bool
    violation: bool

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.legitimate and self.violation:
            raise ValueError("outcome cannot be both legitimate and violation")


@dataclass(frozen=True, slots=True)
class AttackOutcomeScore:
    violation_released: bool
    legitimate_withheld: bool
    terminal: str
    gate_action: str
    attacker_success: bool


def score_trajectory_outcome(
    result: br.TrajectoryResult,
    label: HiddenOutcomeLabel,
) -> AttackOutcomeScore:
    """Score release/hold outcomes against a hidden label only."""

    label.validate()
    released = result.terminal == "release" or (
        result.gate_action == "release" and result.terminal not in {"error"}
    )
    violation_released = bool(label.violation and released)
    legitimate_withheld = bool(label.legitimate and not released)
    if label.violation:
        attacker_success = violation_released
    elif label.legitimate:
        attacker_success = legitimate_withheld
    else:
        attacker_success = False
    return AttackOutcomeScore(
        violation_released=violation_released,
        legitimate_withheld=legitimate_withheld,
        terminal=result.terminal,
        gate_action=result.gate_action,
        attacker_success=attacker_success,
    )


def audit_supervisor_observation(observation: Mapping[str, Any]) -> None:
    """Reject hidden truth fields before a policy or supervisor sees the payload."""

    blob = br.canonical_json(dict(observation))
    for token in br._FORBIDDEN_FEATURE_TOKENS:
        if token in blob:
            raise ValueError(f"observation leaks forbidden field token {token!r}")


# ---------------------------------------------------------------------------
# Exact / capped omission auditor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OmissionWitness:
    """A concrete omission subset and the trajectory it induced."""

    hidden_record_ids: frozenset[str]
    attacker_success: bool
    violation_released: bool
    legitimate_withheld: bool
    terminal: str


@dataclass(frozen=True, slots=True)
class OmissionAuditResult:
    """Worst-case search outcome. Never certifies safety from a truncated search."""

    status: str
    certified: bool
    subsets_examined: int
    subsets_total: int | None
    worst_witness: OmissionWitness | None
    note: str


def _count_allowed_subsets(eligible: Sequence[str]) -> int:
    n = len(eligible)
    if n > 30:
        return -1
    return 1 << n


def _iter_allowed_subsets(eligible: Sequence[str]):
    ids = list(eligible)
    for r in range(len(ids) + 1):
        for combo in itertools.combinations(ids, r):
            yield frozenset(combo)


@dataclass
class ExactOmissionAuditor:
    """Enumerate omission subsets on short logs; cap search on longer logs."""

    max_exact_records: int = 12
    max_subset_examinations: int = 4096

    def allowed_subset_count(self, case: br.EvidenceCase) -> int | None:
        eligible = sorted(eligible_omission_ids(case))
        if len(eligible) > self.max_exact_records:
            return None
        return _count_allowed_subsets(eligible)

    def audit(
        self,
        case: br.EvidenceCase,
        archive: br.TrustedArchive,
        *,
        policy: br.AcquisitionPolicyName,
        budget: br.RecoveryBudget,
        supervisor: br.SupervisorClient,
        label: HiddenOutcomeLabel,
        seed: int = 0,
        ranker: br.LearnedRanker | None = None,
    ) -> OmissionAuditResult:
        eligible = sorted(eligible_omission_ids(case))
        total = _count_allowed_subsets(eligible) if len(eligible) <= self.max_exact_records else None
        if total is None:
            return self._capped_search(
                case,
                archive,
                eligible=eligible,
                policy=policy,
                budget=budget,
                supervisor=supervisor,
                label=label,
                seed=seed,
                ranker=ranker,
                subsets_total=None,
            )
        examined = 0
        worst: OmissionWitness | None = None
        truncated = total > self.max_subset_examinations
        for hidden in _iter_allowed_subsets(eligible):
            if examined >= self.max_subset_examinations:
                truncated = True
                break
            plan = OmissionAttackPlan(hidden_record_ids=hidden)
            attacked = apply_omission_plan(case, plan, archives_bodies=archive.bodies)
            result = br.run_trajectory(
                attacked,
                archive,
                policy,
                budget,
                supervisor,
                seed=seed,
                ranker=ranker,
            )
            obs = br.policy_observation(attacked, result.acquisitions)
            audit_supervisor_observation(obs)
            score = score_trajectory_outcome(result, label)
            witness = OmissionWitness(
                hidden_record_ids=hidden,
                attacker_success=score.attacker_success,
                violation_released=score.violation_released,
                legitimate_withheld=score.legitimate_withheld,
                terminal=result.terminal,
            )
            examined += 1
            if worst is None or (witness.attacker_success and not worst.attacker_success):
                worst = witness
            elif (
                witness.attacker_success
                and worst.attacker_success
                and witness.violation_released
                and not worst.violation_released
            ):
                worst = witness
        if truncated:
            return OmissionAuditResult(
                status="enumeration_truncated",
                certified=False,
                subsets_examined=examined,
                subsets_total=total,
                worst_witness=worst,
                note="search truncated; do not certify from unsuccessful or partial search",
            )
        if worst and worst.attacker_success:
            return OmissionAuditResult(
                status="witness_found",
                certified=True,
                subsets_examined=examined,
                subsets_total=total,
                worst_witness=worst,
                note="exhaustive enumeration on short log",
            )
        return OmissionAuditResult(
            status="no_witness_under_search",
            certified=True,
            subsets_examined=examined,
            subsets_total=total,
            worst_witness=worst,
            note="exhaustive enumeration found no attacker-success witness",
        )

    def _capped_search(
        self,
        case: br.EvidenceCase,
        archive: br.TrustedArchive,
        *,
        eligible: list[str],
        policy: br.AcquisitionPolicyName,
        budget: br.RecoveryBudget,
        supervisor: br.SupervisorClient,
        label: HiddenOutcomeLabel,
        seed: int,
        ranker: br.LearnedRanker | None,
        subsets_total: int | None,
    ) -> OmissionAuditResult:
        rng = random.Random(f"cap:{seed}:{case.workflow_id}")
        examined = 0
        worst: OmissionWitness | None = None
        ids = list(eligible)
        while examined < self.max_subset_examinations:
            k = rng.randrange(len(ids) + 1)
            hidden = frozenset(rng.sample(ids, k) if k else [])
            plan = OmissionAttackPlan(hidden_record_ids=hidden)
            attacked = apply_omission_plan(case, plan, archives_bodies=archive.bodies)
            result = br.run_trajectory(
                attacked,
                archive,
                policy,
                budget,
                supervisor,
                seed=seed,
                ranker=ranker,
            )
            score = score_trajectory_outcome(result, label)
            witness = OmissionWitness(
                hidden_record_ids=hidden,
                attacker_success=score.attacker_success,
                violation_released=score.violation_released,
                legitimate_withheld=score.legitimate_withheld,
                terminal=result.terminal,
            )
            examined += 1
            if worst is None or (witness.attacker_success and not worst.attacker_success):
                worst = witness
        return OmissionAuditResult(
            status="enumeration_truncated",
            certified=False,
            subsets_examined=examined,
            subsets_total=subsets_total,
            worst_witness=worst,
            note="log too long for exact enumeration; capped search only",
        )


def audit_omission_worst_case(
    case: br.EvidenceCase,
    archive: br.TrustedArchive,
    *,
    policy: br.AcquisitionPolicyName,
    budget: br.RecoveryBudget,
    supervisor: br.SupervisorClient,
    label: HiddenOutcomeLabel,
    seed: int = 0,
    max_exact_records: int = 12,
) -> OmissionAuditResult:
    auditor = ExactOmissionAuditor(max_exact_records=max_exact_records)
    return auditor.audit(
        case,
        archive,
        policy=policy,
        budget=budget,
        supervisor=supervisor,
        label=label,
        seed=seed,
    )


def brute_force_worst_witness(
    case: br.EvidenceCase,
    archive: br.TrustedArchive,
    *,
    policy: br.AcquisitionPolicyName,
    budget: br.RecoveryBudget,
    supervisor: br.SupervisorClient,
    label: HiddenOutcomeLabel,
    seed: int = 0,
) -> OmissionWitness | None:
    """Reference exhaustive search for tests (small cases only)."""

    eligible = sorted(eligible_omission_ids(case))
    worst: OmissionWitness | None = None
    for combo in _iter_allowed_subsets(eligible):
        hidden = frozenset(combo)
        plan = OmissionAttackPlan(hidden_record_ids=hidden)
        attacked = apply_omission_plan(case, plan, archives_bodies=archive.bodies)
        result = br.run_trajectory(attacked, archive, policy, budget, supervisor, seed=seed)
        score = score_trajectory_outcome(result, label)
        witness = OmissionWitness(
            hidden_record_ids=hidden,
            attacker_success=score.attacker_success,
            violation_released=score.violation_released,
            legitimate_withheld=score.legitimate_withheld,
            terminal=result.terminal,
        )
        if worst is None or (witness.attacker_success and not worst.attacker_success):
            worst = witness
    return worst


# ---------------------------------------------------------------------------
# Shared workflow-queue budget / timing attack simulator
# ---------------------------------------------------------------------------


WorkCostCallback = Callable[["QueueWorkItem", br.CostLedger], float]
"""Return non-negative measured work (e.g. retrieval seconds) for one queue item."""


@dataclass(frozen=True, slots=True)
class QueueWorkItem:
    case: br.EvidenceCase
    archive: br.TrustedArchive
    label: HiddenOutcomeLabel
    item_id: str
    violation_priority: int = 0


@dataclass(frozen=True, slots=True)
class QueueItemOutcome:
    item_id: str
    terminal: str
    violation_released: bool
    legitimate_withheld: bool
    budget_exhausted_before_service: bool
    measured_work: float


@dataclass(frozen=True, slots=True)
class QueueSimulationResult:
    environment: str
    items: tuple[QueueItemOutcome, ...]
    total_measured_work: float
    shared_budget_exhausted: bool
    note: str


def simulate_shared_budget_queue(
    items: Sequence[QueueWorkItem],
    *,
    shared_budget: br.RecoveryBudget,
    policy: br.AcquisitionPolicyName,
    supervisor: br.SupervisorClient,
    work_cost: WorkCostCallback,
    processing_order: Sequence[str] | None = None,
    seed: int = 0,
) -> QueueSimulationResult:
    """Process a workflow queue under one shared retrieval budget.

    ``work_cost`` must return measured work for the item; the simulator charges
    that amount before running recovery. No arbitrary padding is applied.
    """

    by_id = {item.item_id: item for item in items}
    order = list(processing_order) if processing_order else [item.item_id for item in items]
    if set(order) != set(by_id):
        raise ValueError("processing_order must list exactly the queue item ids")
    ledger = br.CostLedger()
    outcomes: list[QueueItemOutcome] = []
    exhausted = False
    total_work = 0.0
    for item_id in order:
        item = by_id[item_id]
        item.label.validate()
        measured = float(work_cost(item, ledger))
        if measured < 0 or not math.isfinite(measured):
            raise ValueError("work_cost must return finite non-negative seconds")
        if exhausted or not shared_budget.affordable(ledger, retrieval=measured):
            exhausted = True
            outcomes.append(
                QueueItemOutcome(
                    item_id=item_id,
                    terminal="not_serviced",
                    violation_released=False,
                    legitimate_withheld=item.label.legitimate,
                    budget_exhausted_before_service=True,
                    measured_work=0.0,
                )
            )
            continue
        ledger.charge_retrieval(measured)
        total_work += measured
        per_item_budget = br.RecoveryBudget(
            token_credits=shared_budget.token_credits,
            retrieval_seconds=shared_budget.retrieval_seconds - ledger.retrieval_seconds,
            review_seconds=shared_budget.review_seconds,
        )
        result = br.run_trajectory(
            item.case,
            item.archive,
            policy,
            per_item_budget,
            supervisor,
            seed=seed,
        )
        score = score_trajectory_outcome(result, item.label)
        outcomes.append(
            QueueItemOutcome(
                item_id=item_id,
                terminal=result.terminal,
                violation_released=score.violation_released,
                legitimate_withheld=score.legitimate_withheld,
                budget_exhausted_before_service=False,
                measured_work=measured,
            )
        )
        if result.budget_exhausted:
            exhausted = True
    return QueueSimulationResult(
        environment=br.REPLAY_ENVIRONMENT_LABEL,
        items=tuple(outcomes),
        total_measured_work=total_work,
        shared_budget_exhausted=exhausted,
        note="queue simulator charges only work_cost callback amounts",
    )


def attack_queue_order_by_violation_priority(items: Sequence[QueueWorkItem]) -> list[str]:
    """Strategic timing: violation-tagged workflows earlier in the queue."""

    return [
        item.item_id
        for item in sorted(items, key=lambda row: (-row.violation_priority, row.item_id))
    ]


__all__ = [
    "AttackRegime",
    "PublicRecordRef",
    "public_manifest",
    "OmissionAttackPlan",
    "eligible_omission_ids",
    "apply_omission_plan",
    "plan_random_omission",
    "FailureToRecordPlan",
    "apply_failure_to_record",
    "ArchiveUnavailablePlan",
    "apply_archive_unavailable",
    "BodyModificationPlan",
    "apply_body_modification",
    "ReplayAttackPlan",
    "apply_replay_substitution",
    "CompromisedSignerPlan",
    "apply_compromised_signer",
    "AllowedInformation",
    "AllowedInformationCallback",
    "adaptive_omission_plan",
    "HiddenOutcomeLabel",
    "AttackOutcomeScore",
    "score_trajectory_outcome",
    "audit_supervisor_observation",
    "OmissionWitness",
    "OmissionAuditResult",
    "ExactOmissionAuditor",
    "audit_omission_worst_case",
    "brute_force_worst_witness",
    "QueueWorkItem",
    "QueueItemOutcome",
    "QueueSimulationResult",
    "WorkCostCallback",
    "simulate_shared_budget_queue",
    "attack_queue_order_by_violation_priority",
]
