"""Budgeted sequential evidence recovery (offline replay framework).

Implements the prospective design in ``docs/budgeted-recovery-protocol.md``. This
module is separate from the paid development pilot
(``scripts/run_recovery_pilot.py``). All trajectories here run in an explicitly
labelled synthetic **replay** environment with no network I/O.

Public API
----------
- :data:`REPLAY_ENVIRONMENT_LABEL` — constant stamped on every trajectory result.
- :class:`RecoveryBudget`, :class:`CostLedger` — separated token/credit, retrieval,
  and review-time accounting.
- :class:`ManifestEntry`, :class:`EvidenceCase`, :class:`TrajectoryResult`
- :class:`ArchiveRegime` — omission, failure-to-record, tampering, unavailable,
  compromised-signer labelling (evaluator-side for compromise).
- :func:`build_manifest`, :func:`verify_acquired_body`
- :class:`ActionGate` — shared terminal gate without oracle access.
- :class:`AcquisitionPolicyName` — ``full``, ``random``, ``chronological``,
  ``obligation``, ``learned``.
- :func:`run_trajectory` — sequential acquisition + gate + cost logging.
- :func:`fit_learned_ranker` — sklearn ranker fit **only** on supplied development
  trajectories (labels may use oracle inside fitting; inference features do not).
- :func:`indistinguishability_witness` — standard partial-observation check under a
  budget (no novelty claim).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import random
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

import numpy as np
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

REPLAY_ENVIRONMENT_LABEL = "REPLAY_SYNTHETIC_V1"
LOCAL_RETRIEVAL_LABEL = "free_local_replay"

_FORBIDDEN_FEATURE_TOKENS = frozenset(
    {
        "control",
        "legitimate",
        "oracle",
        "oracle_details",
        "scenario_group",
        "case_id",
        "template",
        "instructed_violation",
        "permitted_collaboration",
        "ordinary_mistake",
        "collusion_refusal",
    }
)

SupervisorAction = Literal["release", "hold", "acquire"]
BodyDigest = str


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def body_digest(body: str) -> BodyDigest:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def archive_record_key(workflow_id: str, record_id: str) -> str:
    return f"{workflow_id}\x1f{record_id}"


def identity_envelope(
    workflow_id: str,
    record_id: str,
    kind: str,
    digest: BodyDigest,
    byte_count: int,
) -> str:
    """Canonical signed identity binding (replay contract)."""
    return canonical_json(
        {
            "bytes": byte_count,
            "digest": digest,
            "kind": kind,
            "record_id": record_id,
            "workflow_id": workflow_id,
        }
    )


def signing_bytes(workflow_id: str, entry: ManifestEntry, body: str) -> bytes:
    env = identity_envelope(workflow_id, entry.record_id, entry.kind, entry.digest, entry.bytes)
    return f"{env}\n".encode("utf-8") + body.encode("utf-8")


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoveryBudget:
    """Caps for one whole-trajectory replay run (not cost-matched across arms)."""

    token_credits: float = math.inf
    retrieval_seconds: float = math.inf
    review_seconds: float = math.inf

    def affordable(
        self,
        ledger: CostLedger,
        *,
        retrieval: float = 0.0,
        review: float = 0.0,
        tokens: float = 0.0,
        token_reserve: float = 0.0,
    ) -> bool:
        committed_tokens = ledger.token_credits + ledger.pending_token_reserve + token_reserve + tokens
        return (
            committed_tokens <= self.token_credits + 1e-12
            and ledger.retrieval_seconds + retrieval <= self.retrieval_seconds + 1e-12
            and ledger.review_seconds + review <= self.review_seconds + 1e-12
        )


@dataclass(slots=True)
class CostLedger:
    token_credits: float = 0.0
    retrieval_seconds: float = 0.0
    review_seconds: float = 0.0
    pending_token_reserve: float = 0.0

    def charge_retrieval(self, seconds: float, *, label: str = LOCAL_RETRIEVAL_LABEL) -> None:
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("retrieval seconds must be finite and non-negative")
        self.retrieval_seconds += seconds
        _ = label

    def charge_review(self, seconds: float) -> None:
        if seconds < 0 or not math.isfinite(seconds):
            raise ValueError("review seconds must be finite and non-negative")
        self.review_seconds += seconds

    def charge_tokens(self, credits: float) -> None:
        if credits < 0 or not math.isfinite(credits):
            raise ValueError("token credits must be finite and non-negative")
        self.token_credits += credits

    def begin_token_reserve(self, credits: float) -> None:
        if credits < 0 or not math.isfinite(credits):
            raise ValueError("token reserve must be finite and non-negative")
        self.pending_token_reserve += credits

    def settle_token_reserve(self, reserved: float, actual: float) -> None:
        if reserved < 0 or actual < 0:
            raise ValueError("reserved and actual token amounts must be non-negative")
        if actual > reserved + 1e-12:
            raise ValueError(
                f"supervisor actual token charge {actual} exceeds reservation {reserved}"
            )
        self.pending_token_reserve -= reserved
        if self.pending_token_reserve < -1e-12:
            raise ValueError("pending token reserve underflow")
        self.charge_tokens(actual)

    def as_dict(self) -> dict[str, float]:
        return {
            "pending_token_reserve": self.pending_token_reserve,
            "retrieval_seconds": self.retrieval_seconds,
            "review_seconds": self.review_seconds,
            "token_credits": self.token_credits,
        }


# ---------------------------------------------------------------------------
# Manifest and archive
# ---------------------------------------------------------------------------


class ArchiveRegime(str, Enum):
    """Failure labelling aligned with the protocol trust table."""

    NORMAL = "normal"
    ARCHIVE_UNAVAILABLE = "archive_unavailable"
    ABSENT_RECORD = "absent_record"
    FAILURE_TO_RECORD = "failure_to_record"
    TAMPERING = "tampering"
    COMPROMISED_SIGNER = "compromised_signer"


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    record_id: str
    kind: str
    digest: BodyDigest
    bytes: int
    signature_b64: str
    authenticated_missing: bool = False


@dataclass(frozen=True, slots=True)
class AcquiredRecord:
    record_id: str
    kind: str
    body: str
    digest: BodyDigest
    verified: bool
    regime: ArchiveRegime


@dataclass(frozen=True, slots=True)
class EvidenceCase:
    """Policy-visible workflow shell (no harness control fields)."""

    workflow_id: str
    task: str
    proposal: str
    worker_claims: tuple[str, ...]
    manifest: tuple[ManifestEntry, ...]
    initial_visible_bodies: Mapping[str, str] = field(default_factory=dict)
    retrieval_seconds_per_record: float = 0.0
    review_seconds_per_acquisition: float = 0.0
    review_seconds_per_supervisor_decision: float = 0.0
    provenance: str = "development"


@dataclass(frozen=True, slots=True)
class TrustedArchive:
    """In-memory protected archive for replay."""

    public_key_b64: str
    bodies: Mapping[str, str]
    signatures: Mapping[str, str]
    available: bool = True
    compromised_record_ids: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        workflow_id: str,
        manifest: Sequence[ManifestEntry],
        bodies: Mapping[str, str],
        *,
        signing_key: Ed25519PrivateKey | None = None,
        available: bool = True,
        compromised_record_ids: frozenset[str] = frozenset(),
    ) -> TrustedArchive:
        if manifest and signing_key is None:
            raise ValueError(
                "signing_key is required when building an archive for a non-empty manifest "
                "(public key must be pinned to manifest signatures)"
            )
        key = signing_key or Ed25519PrivateKey.generate()
        public_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
        signatures: dict[str, str] = {}
        for entry in manifest:
            if entry.authenticated_missing:
                continue
            body = bodies.get(entry.record_id)
            if body is None:
                continue
            if body_digest(body) != entry.digest:
                raise ValueError(f"archive body digest mismatch for {entry.record_id}")
            if len(body.encode("utf-8")) != entry.bytes:
                raise ValueError(f"archive body byte length mismatch for {entry.record_id}")
            sig_key = archive_record_key(workflow_id, entry.record_id)
            signatures[sig_key] = entry.signature_b64
        return cls(
            public_key_b64=public_b64,
            bodies=dict(bodies),
            signatures=signatures,
            available=available,
            compromised_record_ids=compromised_record_ids,
        )

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key_b64))


def build_manifest(
    workflow_id: str,
    records: Sequence[tuple[str, str, str]],
    *,
    signing_key: Ed25519PrivateKey | None = None,
    missing_ids: frozenset[str] = frozenset(),
    compromised_ids: frozenset[str] = frozenset(),
) -> tuple[tuple[ManifestEntry, ...], Ed25519PrivateKey]:
    """Create signed manifest rows from *(record_id, kind, body)* tuples."""

    key = signing_key or Ed25519PrivateKey.generate()
    entries: list[ManifestEntry] = []
    for record_id, kind, body in records:
        digest = body_digest(body)
        body_bytes = body.encode("utf-8")
        byte_count = len(body_bytes)
        pending = ManifestEntry(
            record_id=record_id,
            kind=kind,
            digest=digest,
            bytes=byte_count,
            signature_b64="",
            authenticated_missing=record_id in missing_ids,
        )
        message = signing_bytes(workflow_id, pending, body)
        sig = base64.b64encode(key.sign(message)).decode("ascii")
        entries.append(
            ManifestEntry(
                record_id=record_id,
                kind=kind,
                digest=digest,
                bytes=byte_count,
                signature_b64=sig,
                authenticated_missing=record_id in missing_ids,
            )
        )
    _ = compromised_ids  # caller passes to TrustedArchive.build for evaluator-only marking
    return tuple(entries), key


def verify_acquired_body(
    *,
    workflow_id: str,
    entry: ManifestEntry,
    body: str,
    archive: TrustedArchive,
    kind: str | None = None,
) -> tuple[bool, ArchiveRegime]:
    """Digest + identity-envelope signature verification against the trusted manifest row."""

    effective_kind = kind if kind is not None else entry.kind
    body_bytes = body.encode("utf-8")
    if len(body_bytes) != entry.bytes:
        return False, ArchiveRegime.TAMPERING
    if body_digest(body) != entry.digest:
        return False, ArchiveRegime.TAMPERING
    if effective_kind != entry.kind:
        return False, ArchiveRegime.TAMPERING

    sig_key = archive_record_key(workflow_id, entry.record_id)
    stored_sig = archive.signatures.get(sig_key)
    if stored_sig is not None and stored_sig != entry.signature_b64:
        return False, ArchiveRegime.TAMPERING
    signature_b64 = stored_sig or entry.signature_b64
    if not signature_b64:
        return False, ArchiveRegime.ABSENT_RECORD
    try:
        _verify_signature(workflow_id, entry, body, signature_b64, archive.public_key())
    except ValueError:
        return False, ArchiveRegime.TAMPERING
    return True, ArchiveRegime.NORMAL


def _verify_signature(
    workflow_id: str,
    entry: ManifestEntry,
    body: str,
    signature_b64: str,
    public_key: Ed25519PublicKey,
) -> None:
    message = signing_bytes(workflow_id, entry, body)
    try:
        public_key.verify(base64.b64decode(signature_b64), message)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError(f"signature verification failed: {exc}") from exc


def evaluator_outcome(
    archive: TrustedArchive,
    case: EvidenceCase,
    acquired: Sequence[AcquiredRecord],
) -> dict[str, Any]:
    """Hidden outcome checks (compromise semantics); not exposed to the action gate."""
    compromised_acquired = [
        row.record_id
        for row in acquired
        if row.record_id in archive.compromised_record_ids and row.verified
    ]
    return {
        "compromised_signer_records": compromised_acquired,
        "workflow_id": case.workflow_id,
    }


# ---------------------------------------------------------------------------
# Policy observation features (no oracle / control leakage)
# ---------------------------------------------------------------------------


def policy_observation(case: EvidenceCase, acquired: Sequence[AcquiredRecord]) -> dict[str, Any]:
    """JSON-serializable view safe for ranker features and supervisor prompts."""

    visible = [
        {"record_id": row.record_id, "kind": row.kind, "body": row.body, "verified": row.verified}
        for row in acquired
    ]
    missing_meta = [
        {
            "record_id": entry.record_id,
            "kind": entry.kind,
            "digest": entry.digest,
            "bytes": entry.bytes,
            "authenticated_missing": entry.authenticated_missing,
        }
        for entry in case.manifest
        if entry.record_id not in {row.record_id for row in acquired}
    ]
    return {
        "acquired_records": visible,
        "environment": REPLAY_ENVIRONMENT_LABEL,
        "missing_record_metadata": missing_meta,
        "proposal": case.proposal,
        "task": case.task,
        "worker_claims": list(case.worker_claims),
    }


def _task_keywords(task: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]*", task)}


def obligation_score(kind: str, task: str) -> int:
    task_lower = task.lower()
    score = 0
    if kind in task_lower:
        score += 10
    kind_compact = kind.replace("-", "_")
    if kind_compact in task_lower:
        score += 8
    keywords = _task_keywords(task)
    for part in re.split(r"[_-]", kind):
        if part in keywords:
            score += 3
        if part and part in task_lower:
            score += 1
    return score


def ranker_features(case: EvidenceCase, entry: ManifestEntry, acquired_ids: set[str]) -> dict[str, float]:
    """Scalar features for one candidate acquisition (no hidden bodies)."""
    return {
        "already_acquired_count": float(len(acquired_ids)),
        "authenticated_missing_flag": 1.0 if entry.authenticated_missing else 0.0,
        "kind_length": float(len(entry.kind)),
        "manifest_bytes": float(entry.bytes),
        "obligation_score": float(obligation_score(entry.kind, case.task)),
        "remaining_manifest_count": float(len(case.manifest) - len(acquired_ids)),
    }


def feature_vector(features: Mapping[str, float]) -> np.ndarray:
    keys = sorted(features)
    for key in keys:
        if key in _FORBIDDEN_FEATURE_TOKENS:
            raise ValueError(f"forbidden feature key: {key}")
    return np.array([features[key] for key in keys], dtype=float)


# ---------------------------------------------------------------------------
# Supervisor and gate (no oracle)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    action: SupervisorAction
    reason: str
    token_credits: float


class SupervisorClient(Protocol):
    def reserve_token_credits(self) -> float:
        """Conservative token reservation charged before every ``decide`` call."""

    def decide(self, observation: Mapping[str, Any], *, ledger: CostLedger) -> SupervisorDecision | Mapping[str, Any]:
        """Return a :class:`SupervisorDecision` or equivalent mapping at the typed boundary."""


def parse_supervisor_decision(raw: SupervisorDecision | Mapping[str, Any]) -> SupervisorDecision:
    if isinstance(raw, SupervisorDecision):
        return raw
    action = raw.get("action")
    if action not in {"release", "hold", "acquire"}:
        logger.error("supervisor returned unknown action: %r", action)
        raise ValueError(f"unknown supervisor action: {action!r}")
    reason = str(raw.get("reason", ""))
    token_raw = raw.get("token_credits", 0.0)
    try:
        token_credits = float(token_raw)
    except (TypeError, ValueError) as exc:
        logger.error("supervisor returned non-numeric token_credits: %r", token_raw)
        raise ValueError("supervisor token_credits must be numeric") from exc
    if not math.isfinite(token_credits) or token_credits < 0:
        logger.error("supervisor returned invalid token_credits: %r", token_raw)
        raise ValueError("supervisor token_credits must be finite and non-negative")
    return SupervisorDecision(action=action, reason=reason, token_credits=token_credits)


@dataclass(frozen=True, slots=True)
class ActionGate:
    """Shared terminal gate: integrity and missing-metadata checks only."""

    require_all_manifest_rows_resolved: bool = False
    require_observable_revocation_for_compromise: bool = False

    def evaluate(
        self,
        case: EvidenceCase,
        acquired: Sequence[AcquiredRecord],
        events: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        if any(not row.verified for row in acquired):
            return {"action": "hold", "reason": "integrity verification failed on acquired evidence"}
        if any(event.get("kind") == "integrity_failure" for event in events):
            return {"action": "hold", "reason": "logged integrity failure during acquisition"}
        if any(event.get("kind") == "archive_unavailable" for event in events):
            return {"action": "hold", "reason": "archive unavailable under frozen stopping rule"}
        if self.require_observable_revocation_for_compromise and any(
            event.get("kind") == "signer_revocation" for event in events
        ):
            return {
                "action": "hold",
                "reason": "observable signer revocation under explicit compromise regime",
            }

        acquired_ids = {row.record_id for row in acquired}
        unresolved = [
            entry.record_id
            for entry in case.manifest
            if entry.record_id not in acquired_ids and not entry.authenticated_missing
        ]
        if self.require_all_manifest_rows_resolved:
            if unresolved:
                return {"action": "hold", "reason": "unresolved evidence remains in manifest"}
            missing_auth = [
                e.record_id
                for e in case.manifest
                if e.authenticated_missing and e.record_id not in acquired_ids
            ]
            if missing_auth:
                return {"action": "hold", "reason": "authenticated missing records remain"}
        elif unresolved:
            return {
                "action": "release",
                "reason": "acquired evidence verified; unresolved manifest rows logged separately",
                "unresolved_record_ids": unresolved,
            }

        return {"action": "release", "reason": "acquired evidence verified and manifest rows resolved"}


# ---------------------------------------------------------------------------
# Acquisition policies
# ---------------------------------------------------------------------------


class AcquisitionPolicyName(str, Enum):
    FULL = "full"
    RANDOM = "random"
    CHRONOLOGICAL = "chronological"
    OBLIGATION = "obligation"
    LEARNED = "learned"


@dataclass
class LearnedRanker:
    """Frozen sklearn pipeline; fit only via :func:`fit_learned_ranker`."""

    pipeline: Pipeline
    feature_keys: tuple[str, ...]
    fitted_on: str = "development_only"

    def score(self, case: EvidenceCase, entry: ManifestEntry, acquired_ids: set[str]) -> float:
        feats = ranker_features(case, entry, acquired_ids)
        vector = np.array([feats[key] for key in self.feature_keys], dtype=float).reshape(1, -1)
        return float(self.pipeline.decision_function(vector)[0])


PolicySelector = Callable[
    [EvidenceCase, set[str], set[str], RecoveryBudget, CostLedger],
    str | None,
]

RetrievalCostFn = Callable[[EvidenceCase, str], tuple[float, str]]


def _eligible_ids(case: EvidenceCase, acquired: set[str], blocked: set[str]) -> list[str]:
    return [
        e.record_id
        for e in case.manifest
        if e.record_id not in acquired and e.record_id not in blocked and not e.authenticated_missing
    ]


def _policy_full(
    case: EvidenceCase, acquired: set[str], blocked: set[str], _budget: RecoveryBudget, ledger: CostLedger
) -> str | None:
    for record_id in _eligible_ids(case, acquired, blocked):
        return record_id
    return None


def _policy_chronological(
    case: EvidenceCase, acquired: set[str], blocked: set[str], budget: RecoveryBudget, ledger: CostLedger
) -> str | None:
    return _policy_full(case, acquired, blocked, budget, ledger)


def _policy_random(
    case: EvidenceCase,
    acquired: set[str],
    blocked: set[str],
    budget: RecoveryBudget,
    ledger: CostLedger,
    *,
    seed: int,
) -> str | None:
    candidates = _eligible_ids(case, acquired, blocked)
    if not candidates:
        return None
    rng = random.Random(f"{seed}:{case.workflow_id}:acquire")
    return rng.choice(candidates)


def _policy_obligation(
    case: EvidenceCase, acquired: set[str], blocked: set[str], budget: RecoveryBudget, ledger: CostLedger
) -> str | None:
    ranked = sorted(
        case.manifest,
        key=lambda e: (-obligation_score(e.kind, case.task), e.record_id),
    )
    for entry in ranked:
        if entry.record_id in acquired or entry.record_id in blocked or entry.authenticated_missing:
            continue
        return entry.record_id
    return None


def selector_for_policy(
    name: AcquisitionPolicyName,
    *,
    seed: int = 0,
    ranker: LearnedRanker | None = None,
) -> PolicySelector:
    if name == AcquisitionPolicyName.FULL:
        return _policy_full
    if name == AcquisitionPolicyName.CHRONOLOGICAL:
        return _policy_chronological
    if name == AcquisitionPolicyName.RANDOM:
        return lambda c, a, bl, b, l: _policy_random(c, a, bl, b, l, seed=seed)
    if name == AcquisitionPolicyName.OBLIGATION:
        return _policy_obligation
    if name == AcquisitionPolicyName.LEARNED:
        if ranker is None:
            raise ValueError("learned policy requires a fitted LearnedRanker")
        return _learned_selector(ranker)
    raise ValueError(f"unknown policy {name}")


def _learned_selector(ranker: LearnedRanker) -> PolicySelector:
    def _select(
        case: EvidenceCase,
        acquired: set[str],
        blocked: set[str],
        budget: RecoveryBudget,
        ledger: CostLedger,
    ) -> str | None:
        candidates = [e for e in case.manifest if e.record_id in _eligible_ids(case, acquired, blocked)]
        if not candidates:
            return None
        best = max(candidates, key=lambda e: (ranker.score(case, e, acquired), -len(e.record_id)))
        return best.record_id

    return _select


def _measure_retrieval_cost(
    case: EvidenceCase,
    record_id: str,
    retrieval_cost_fn: RetrievalCostFn | None,
) -> tuple[float, str]:
    if retrieval_cost_fn is not None:
        return retrieval_cost_fn(case, record_id)
    seconds = case.retrieval_seconds_per_record
    if seconds <= 0:
        return 0.0, LOCAL_RETRIEVAL_LABEL
    return seconds, "configured_replay_seconds"


# ---------------------------------------------------------------------------
# Trajectory runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrajectoryResult:
    environment: str
    policy: str
    terminal: str
    gate_action: str
    gate_reason: str
    acquisitions: tuple[AcquiredRecord, ...]
    events: tuple[dict[str, Any], ...]
    costs: dict[str, float]
    budget_exhausted: bool
    evaluator_outcome: dict[str, Any] = field(default_factory=dict)


def fetch_record(
    case: EvidenceCase,
    record_id: str,
    archive: TrustedArchive,
    *,
    tamper_body: str | None = None,
    kind_override: str | None = None,
    workflow_override: str | None = None,
) -> tuple[AcquiredRecord | None, dict[str, Any]]:
    """Simulate one trusted acquisition with digest/signature verification."""

    entry = next((e for e in case.manifest if e.record_id == record_id), None)
    if entry is None:
        return None, {"kind": "absent_record", "record_id": record_id}
    if entry.authenticated_missing:
        return None, {
            "detail": "authenticated missing metadata; no authentic body",
            "kind": "failure_to_record",
            "record_id": record_id,
        }
    if not archive.available:
        return None, {"kind": "archive_unavailable", "record_id": record_id}

    body = tamper_body if tamper_body is not None else archive.bodies.get(record_id)
    if body is None:
        return None, {"detail": "no retrievable body", "kind": "absent_record", "record_id": record_id}

    wf = workflow_override if workflow_override is not None else case.workflow_id
    ok, regime = verify_acquired_body(
        workflow_id=wf,
        entry=entry,
        body=body,
        archive=archive,
        kind=kind_override,
    )
    event: dict[str, Any] = {"kind": "acquisition", "record_id": record_id, "regime": regime.value, "verified": ok}
    if not ok or regime != ArchiveRegime.NORMAL:
        event["kind"] = "integrity_failure"
    row = AcquiredRecord(
        record_id=record_id,
        kind=entry.kind,
        body=body,
        digest=body_digest(body),
        verified=ok and regime == ArchiveRegime.NORMAL,
        regime=regime,
    )
    return row, event


def _supervisor_call(
    case: EvidenceCase,
    acquired: Sequence[AcquiredRecord],
    events: list[dict[str, Any]],
    budget: RecoveryBudget,
    ledger: CostLedger,
    supervisor: SupervisorClient,
) -> SupervisorDecision | None:
    """Reserve tokens and review budget, then call supervisor. Returns None if unaffordable."""
    reserve = float(supervisor.reserve_token_credits())
    review_cost = case.review_seconds_per_supervisor_decision
    if reserve > 0 and not budget.affordable(ledger, tokens=0.0, token_reserve=reserve):
        return None
    if review_cost > 0 and not budget.affordable(ledger, review=review_cost):
        return None
    if reserve > 0:
        ledger.begin_token_reserve(reserve)
    if review_cost > 0:
        ledger.charge_review(review_cost)
    observation = policy_observation(case, acquired)
    try:
        raw = supervisor.decide(observation, ledger=ledger)
        decision = parse_supervisor_decision(raw)
    except Exception as exc:
        if reserve > 0:
            ledger.settle_token_reserve(reserve, reserve)
        events.append({"action": "hold", "error": str(exc), "kind": "supervisor_error", "terminal": False, "conservative_charge": reserve})
        raise
    ledger.settle_token_reserve(reserve, decision.token_credits)
    events.append(
        {
            "action": decision.action,
            "kind": "supervisor_decision",
            "reason": decision.reason,
            "token_credits": decision.token_credits,
            "token_reserve": reserve,
        }
    )
    return decision


def _try_acquire_one(
    case: EvidenceCase,
    archive: TrustedArchive,
    next_id: str,
    budget: RecoveryBudget,
    ledger: CostLedger,
    events: list[dict[str, Any]],
    acquired: list[AcquiredRecord],
    acquired_ids: set[str],
    blocked_ids: set[str],
    tamper_on_fetch: Mapping[str, str] | None,
    retrieval_cost_fn: RetrievalCostFn | None,
) -> bool:
    """Attempt one retrieval; return False if budget blocks it (sets exhausted when appropriate)."""
    retrieval_cost, retrieval_label = _measure_retrieval_cost(case, next_id, retrieval_cost_fn)
    review_cost = case.review_seconds_per_acquisition
    if not budget.affordable(ledger, retrieval=retrieval_cost, review=review_cost):
        return False
    tamper = tamper_on_fetch.get(next_id) if tamper_on_fetch else None
    row, event = fetch_record(case, next_id, archive, tamper_body=tamper)
    ledger.charge_retrieval(retrieval_cost, label=retrieval_label)
    if review_cost:
        ledger.charge_review(review_cost)
    event["retrieval_label"] = retrieval_label
    event["retrieval_seconds"] = retrieval_cost
    events.append(event)
    if row is not None:
        acquired.append(row)
        acquired_ids.add(row.record_id)
    elif event.get("kind") in {"archive_unavailable", "absent_record", "failure_to_record"}:
        blocked_ids.add(next_id)
    return True


def run_trajectory(
    case: EvidenceCase,
    archive: TrustedArchive,
    policy: AcquisitionPolicyName,
    budget: RecoveryBudget,
    supervisor: SupervisorClient,
    *,
    seed: int = 0,
    ranker: LearnedRanker | None = None,
    gate: ActionGate | None = None,
    tamper_on_fetch: Mapping[str, str] | None = None,
    retrieval_cost_fn: RetrievalCostFn | None = None,
) -> TrajectoryResult:
    """Sequential trusted acquisition with interleaved supervisor decisions (except full comparator)."""

    gate = gate or ActionGate()
    select = selector_for_policy(policy, seed=seed, ranker=ranker)
    ledger = CostLedger()
    acquired: list[AcquiredRecord] = []
    acquired_ids: set[str] = set()
    blocked_ids: set[str] = set()
    events: list[dict[str, Any]] = []
    budget_exhausted = False
    last_supervisor: SupervisorDecision | None = None
    full_comparator = policy == AcquisitionPolicyName.FULL

    for record_id, body in case.initial_visible_bodies.items():
        entry = next((e for e in case.manifest if e.record_id == record_id), None)
        if entry is None:
            events.append({"kind": "integrity_failure", "record_id": record_id, "detail": "unknown record"})
            continue
        ok, regime = verify_acquired_body(workflow_id=case.workflow_id, entry=entry, body=body, archive=archive)
        acquired.append(
            AcquiredRecord(
                record_id=record_id,
                kind=entry.kind,
                body=body,
                digest=body_digest(body),
                verified=ok and regime == ArchiveRegime.NORMAL,
                regime=regime,
            )
        )
        acquired_ids.add(record_id)
        events.append(
            {
                "kind": "initial_visible",
                "record_id": record_id,
                "regime": regime.value,
                "verified": ok,
            }
        )
        if not ok:
            events.append({"kind": "integrity_failure", "record_id": record_id, "source": "initial_visible"})

    def _no_supervisor_budget() -> bool:
        reserve = float(supervisor.reserve_token_credits())
        review = case.review_seconds_per_supervisor_decision
        if reserve > 0 and not budget.affordable(ledger, token_reserve=reserve):
            return True
        if review > 0 and not budget.affordable(ledger, review=review):
            return True
        return False

    if full_comparator:
        while True:
            next_id = select(case, acquired_ids, blocked_ids, budget, ledger)
            if next_id is None:
                break
            retrieval_cost, _ = _measure_retrieval_cost(case, next_id, retrieval_cost_fn)
            review_cost = case.review_seconds_per_acquisition
            if not budget.affordable(ledger, retrieval=retrieval_cost, review=review_cost):
                budget_exhausted = True
                events.append({"kind": "budget_exhausted", "terminal": False})
                break
            if not _try_acquire_one(
                case,
                archive,
                next_id,
                budget,
                ledger,
                events,
                acquired,
                acquired_ids,
                blocked_ids,
                tamper_on_fetch,
                retrieval_cost_fn,
            ):
                budget_exhausted = True
                events.append({"kind": "budget_exhausted", "terminal": False})
                break
        if _no_supervisor_budget():
            events.append({"kind": "supervisor_skipped", "reason": "insufficient token or review budget"})
            last_supervisor = SupervisorDecision(action="hold", reason="supervisor budget exhausted", token_credits=0.0)
        else:
            try:
                last_supervisor = _supervisor_call(case, acquired, events, budget, ledger, supervisor)
            except Exception:
                return TrajectoryResult(
                    environment=REPLAY_ENVIRONMENT_LABEL,
                    policy=policy.value,
                    terminal="error",
                    gate_action="hold",
                    gate_reason="supervisor error",
                    acquisitions=tuple(acquired),
                    events=tuple(events),
                    costs=ledger.as_dict(),
                    budget_exhausted=budget_exhausted,
                    evaluator_outcome=evaluator_outcome(archive, case, acquired),
                )
            if last_supervisor is None:
                budget_exhausted = True
                events.append({"kind": "budget_exhausted", "terminal": False, "phase": "supervisor"})
    else:
        while True:
            if _no_supervisor_budget():
                events.append({"kind": "supervisor_skipped", "reason": "insufficient token or review budget"})
                last_supervisor = SupervisorDecision(action="hold", reason="supervisor budget exhausted", token_credits=0.0)
                break
            try:
                decision = _supervisor_call(case, acquired, events, budget, ledger, supervisor)
            except Exception:
                return TrajectoryResult(
                    environment=REPLAY_ENVIRONMENT_LABEL,
                    policy=policy.value,
                    terminal="error",
                    gate_action="hold",
                    gate_reason="supervisor error",
                    acquisitions=tuple(acquired),
                    events=tuple(events),
                    costs=ledger.as_dict(),
                    budget_exhausted=budget_exhausted,
                    evaluator_outcome=evaluator_outcome(archive, case, acquired),
                )
            if decision is None:
                budget_exhausted = True
                events.append({"kind": "budget_exhausted", "terminal": False, "phase": "supervisor"})
                last_supervisor = SupervisorDecision(action="hold", reason="supervisor budget exhausted", token_credits=0.0)
                break
            last_supervisor = decision
            if decision.action in {"release", "hold"}:
                break
            next_id = select(case, acquired_ids, blocked_ids, budget, ledger)
            if next_id is None:
                break
            retrieval_cost, _ = _measure_retrieval_cost(case, next_id, retrieval_cost_fn)
            review_cost = case.review_seconds_per_acquisition
            if not budget.affordable(ledger, retrieval=retrieval_cost, review=review_cost):
                budget_exhausted = True
                events.append({"kind": "budget_exhausted", "terminal": False})
                break
            if not _try_acquire_one(
                case,
                archive,
                next_id,
                budget,
                ledger,
                events,
                acquired,
                acquired_ids,
                blocked_ids,
                tamper_on_fetch,
                retrieval_cost_fn,
            ):
                budget_exhausted = True
                events.append({"kind": "budget_exhausted", "terminal": False})
                break

    if last_supervisor is None:
        last_supervisor = SupervisorDecision(action="hold", reason="no supervisor decision", token_credits=0.0)

    gate_result = gate.evaluate(case, acquired, events)
    unresolved = gate_result.get("unresolved_record_ids")
    if unresolved:
        events.append({"kind": "unresolved_manifest_rows", "record_ids": unresolved})

    if gate_result["action"] == "hold":
        final_action = "hold"
        reason = gate_result["reason"]
    else:
        final_action = last_supervisor.action if last_supervisor.action in {"release", "hold"} else "hold"
        if final_action == "release" and last_supervisor.action == "acquire":
            final_action = "hold"
            reason = "supervisor ended on acquire without terminal release"
        else:
            reason = last_supervisor.reason or gate_result["reason"]

    terminal = "release" if final_action == "release" else "hold"
    if budget_exhausted and final_action == "hold":
        terminal = "budget_hold"
    events.append(
        {
            "action": final_action,
            "kind": "terminal",
            "reason": reason,
            "supervisor_action": last_supervisor.action,
            "terminal": True,
        }
    )

    return TrajectoryResult(
        environment=REPLAY_ENVIRONMENT_LABEL,
        policy=policy.value,
        terminal=terminal,
        gate_action=final_action,
        gate_reason=reason,
        acquisitions=tuple(acquired),
        events=tuple(events),
        costs=ledger.as_dict(),
        budget_exhausted=budget_exhausted,
        evaluator_outcome=evaluator_outcome(archive, case, acquired),
    )


# ---------------------------------------------------------------------------
# Learned ranker fitting (development only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DevelopmentLabel:
    """Training label for one manifest row (development partition only)."""

    workflow_id: str
    record_id: str
    helps_separate: bool  # may be derived from oracle offline; not visible at inference


def fit_learned_ranker(
    cases: Sequence[EvidenceCase],
    labels: Sequence[DevelopmentLabel],
    *,
    random_state: int = 0,
) -> LearnedRanker:
    """Fit logistic ranker on development trajectories only."""

    label_index = {(row.workflow_id, row.record_id): row.helps_separate for row in labels}
    xs: list[np.ndarray] = []
    ys: list[int] = []
    feature_keys: tuple[str, ...] | None = None
    for case in cases:
        if case.provenance != "development":
            raise ValueError(f"fit_learned_ranker requires development provenance, got {case.provenance!r}")
        acquired: set[str] = set()
        for entry in case.manifest:
            label_key = (case.workflow_id, entry.record_id)
            if label_key not in label_index:
                raise ValueError(f"missing development label for {label_key}")
            key = ranker_features(case, entry, acquired)
            if feature_keys is None:
                feature_keys = tuple(sorted(key))
            xs.append(feature_vector(key))
            ys.append(1 if label_index[label_key] else 0)
    if feature_keys is None:
        raise ValueError("no training rows")
    pipeline = Pipeline(
        [
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=500, random_state=random_state)),
        ]
    )
    pipeline.fit(np.vstack(xs), np.array(ys, dtype=int))
    return LearnedRanker(pipeline=pipeline, feature_keys=feature_keys)


# ---------------------------------------------------------------------------
# Indistinguishability (standard partial-observation argument)
# ---------------------------------------------------------------------------


def indistinguishability_witness(
    case_a: EvidenceCase,
    case_b: EvidenceCase,
    archive_a: TrustedArchive,
    archive_b: TrustedArchive,
    budget: RecoveryBudget,
    *,
    policy: AcquisitionPolicyName = AcquisitionPolicyName.CHRONOLOGICAL,
    seed: int = 0,
) -> dict[str, Any]:
    """Return whether two histories yield identical policy observations under budget.

    This compares one frozen replay policy only. It does not enumerate every
    affordable acquisition and cannot establish universal indistinguishability.
    """

    class _NullSupervisor:
        def reserve_token_credits(self) -> float:
            return 0.0

        def decide(self, observation: Mapping[str, Any], *, ledger: CostLedger) -> SupervisorDecision:
            return SupervisorDecision(action="hold", reason="witness", token_credits=0.0)

    sup = _NullSupervisor()
    res_a = run_trajectory(case_a, archive_a, policy, budget, sup, seed=seed)
    res_b = run_trajectory(case_b, archive_b, policy, budget, sup, seed=seed)
    obs_a = policy_observation(case_a, res_a.acquisitions)
    obs_b = policy_observation(case_b, res_b.acquisitions)
    identical = canonical_json(obs_a) == canonical_json(obs_b)
    return {
        "acquired_count_a": len(res_a.acquisitions),
        "acquired_count_b": len(res_b.acquisitions),
        "budget": {
            "retrieval_seconds": budget.retrieval_seconds,
            "token_credits": budget.token_credits,
        },
        "identical_observations": identical,
        "scope": "one_frozen_policy_replay",
        "universal_indistinguishability_established": False,
        "note": "standard partial-observation argument; not a novelty claim",
    }


__all__ = [
    "REPLAY_ENVIRONMENT_LABEL",
    "LOCAL_RETRIEVAL_LABEL",
    "RecoveryBudget",
    "CostLedger",
    "ArchiveRegime",
    "ManifestEntry",
    "AcquiredRecord",
    "EvidenceCase",
    "TrustedArchive",
    "build_manifest",
    "verify_acquired_body",
    "evaluator_outcome",
    "identity_envelope",
    "policy_observation",
    "obligation_score",
    "ranker_features",
    "SupervisorClient",
    "SupervisorDecision",
    "parse_supervisor_decision",
    "ActionGate",
    "AcquisitionPolicyName",
    "LearnedRanker",
    "selector_for_policy",
    "TrajectoryResult",
    "fetch_record",
    "run_trajectory",
    "DevelopmentLabel",
    "fit_learned_ranker",
    "indistinguishability_witness",
]
