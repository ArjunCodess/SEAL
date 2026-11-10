"""Supervised recovery baselines: TF-IDF and frozen precomputed embeddings.

Development-fit only. Inference uses policy-visible text or supplied embedding
vectors with provenance; TF-IDF is not represented as embeddings.

Public API
----------
- :data:`BASELINE_KIND_TFIDF`, :data:`BASELINE_KIND_FROZEN_EMBEDDING`
- :data:`DEVELOPMENT_FIT_LABEL`, :data:`DEVELOPMENT_TRAINING_SPLITS`
- :data:`FORBIDDEN_POLICY_FEATURE_KEYS` — oracle/control/template leakage guard
- :data:`HUMAN_REVIEW_DECISIONS` — allowed measured human review outcomes
- :class:`DevelopmentTrainingProvenance` — pinned split/group/data digests for ``fit``
- :func:`observation_to_text` — serialize a policy-safe observation for lexical models
- :func:`audit_policy_payload` — reject forbidden keys in nested dicts
- :func:`audit_group_split` — ensure partition groups never cross learn/cal/test
- :class:`TfidfSupervisedBaseline` — fit/transform on development text only
- :class:`EmbeddingProvenance`, :class:`FrozenEmbeddingClassifier`
- :class:`HumanReviewRecord`, :class:`HumanReviewLog`

Limitations
-----------
- Labels may use oracle information **only** inside ``fit``; callers must not pass
  oracle fields into ``predict`` inputs.
- ``HumanReviewLog`` never imputes missing reviewer fields from oracle outcomes.
- No network calls; embedding vectors must be precomputed offline by the caller.
- Group identifiers used in ``audit_group_split`` are audit metadata and must not
  be attached to policy observations.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.special import expit
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

BASELINE_KIND_TFIDF = "tfidf_supervised_logistic_v1"
BASELINE_KIND_FROZEN_EMBEDDING = "frozen_embedding_logistic_v1"

DEVELOPMENT_FIT_LABEL = "development_only"
DEVELOPMENT_TRAINING_SPLITS = frozenset({"learn"})

HUMAN_REVIEW_DECISIONS = frozenset({"hold", "release", "escalate", "defer"})

FORBIDDEN_POLICY_FEATURE_KEYS = frozenset(
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
        "partition_group",
        "violation",
        "unsafe",
    }
)

_TEXT_KEY_ORDER = ("task", "proposal", "worker_claims", "acquired_records", "records")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_digest_hex(name: str, value: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"{name} must be a 64-character lowercase hex sha256 digest")


def _digest_ndarray(arr: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(arr)
    return _sha256_bytes(contiguous.tobytes())


def _digest_vocabulary(vocabulary: Mapping[str, int]) -> str:
    ordered = [(term, int(index)) for term, index in sorted(vocabulary.items(), key=lambda item: item[0])]
    return _sha256_hex(canonical_json(ordered))


def _normalize_opaque_group_ids(groups: Sequence[str], *, context: str) -> tuple[str, ...]:
    if not groups:
        raise ValueError(f"{context}: training_groups must be non-empty")
    normalized: list[str] = []
    seen: set[str] = set()
    for group_id in groups:
        if not group_id or group_id.strip() != group_id:
            raise ValueError(f"{context}: partition group ids must be non-empty opaque strings")
        if group_id not in seen:
            seen.add(group_id)
            normalized.append(group_id)
    return tuple(normalized)


def _digest_training_groups(groups: Sequence[str]) -> str:
    unique = _normalize_opaque_group_ids(groups, context="training_groups")
    return _sha256_hex(canonical_json(sorted(unique)))


def _digest_labeled_texts(texts: Sequence[str], labels: Sequence[int]) -> str:
    return _sha256_hex(
        canonical_json({"texts": list(texts), "labels": [int(v) for v in labels]})
    )


def _digest_labeled_embeddings(embeddings: np.ndarray, labels: Sequence[int]) -> str:
    return _sha256_hex(
        canonical_json(
            {
                "embeddings": _digest_ndarray(np.asarray(embeddings, dtype=float)),
                "labels": [int(v) for v in labels],
            }
        )
    )


def _validate_binary_labels(labels: Sequence[int], *, context: str) -> np.ndarray:
    y = np.asarray(labels, dtype=int)
    if y.ndim != 1:
        raise ValueError(f"{context}: labels must be 1-D")
    if not np.all(np.isin(y, (0, 1))):
        raise ValueError(f"{context}: labels must be binary 0/1")
    if y.size == 0:
        raise ValueError(f"{context}: labels must be non-empty")
    if len(np.unique(y)) < 2:
        raise ValueError(f"{context}: labels must contain both classes")
    return y


def _assert_disjoint_groups(
    training_groups: Sequence[str],
    *,
    calibration_groups: Sequence[str] | None,
    test_groups: Sequence[str] | None,
) -> None:
    train = set(training_groups)
    if calibration_groups is not None:
        overlap = train & set(calibration_groups)
        if overlap:
            raise ValueError(f"training groups overlap declared calibration groups: {sorted(overlap)}")
    if test_groups is not None:
        overlap = train & set(test_groups)
        if overlap:
            raise ValueError(f"training groups overlap declared test groups: {sorted(overlap)}")


def _assert_development_fit(*, fitted_on: str, provenance: DevelopmentTrainingProvenance) -> None:
    if fitted_on != DEVELOPMENT_FIT_LABEL:
        raise ValueError(
            f"supervised baselines may only be fitted with fitted_on={DEVELOPMENT_FIT_LABEL!r}"
        )
    if provenance.split_label not in DEVELOPMENT_TRAINING_SPLITS:
        raise ValueError(
            "development fitting requires an explicit learn-split provenance; "
            f"got split_label={provenance.split_label!r}"
        )


def audit_policy_payload(payload: Mapping[str, Any], *, context: str = "observation") -> None:
    """Raise if forbidden oracle/control keys appear anywhere in a nested mapping."""

    def walk(obj: Any, path: str) -> None:
        if isinstance(obj, Mapping):
            for key, value in obj.items():
                key_l = str(key).lower()
                if key_l in FORBIDDEN_POLICY_FEATURE_KEYS:
                    raise ValueError(f"{context}: forbidden key {key!r} at {path}")
                walk(value, f"{path}.{key}")
        elif isinstance(obj, (list, tuple)):
            for idx, item in enumerate(obj):
                walk(item, f"{path}[{idx}]")

    walk(payload, context)


def observation_to_text(observation: Mapping[str, Any]) -> str:
    """Concatenate policy-visible fields into one document (no forbidden keys)."""

    audit_policy_payload(observation)
    chunks: list[str] = []
    for key in _TEXT_KEY_ORDER:
        if key not in observation:
            continue
        value = observation[key]
        if key == "worker_claims" and isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            chunks.extend(str(item) for item in value)
        elif key in {"acquired_records", "records"} and isinstance(value, Sequence):
            for row in value:
                if isinstance(row, Mapping):
                    kind = row.get("kind", "")
                    body = row.get("body", "")
                    chunks.append(f"{kind}\n{body}")
                else:
                    chunks.append(str(row))
        else:
            chunks.append(str(value))
    text = "\n".join(part for part in chunks if part)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def audit_group_split(
    assignments: Mapping[str, str] | Sequence[tuple[str, str]],
    *,
    allowed_labels: frozenset[str] = frozenset({"learn", "calibration", "test", "holdout"}),
) -> dict[str, Any]:
    """Verify each partition group maps to exactly one split label.

    Accepts either a mapping or an explicit sequence of ``(group_id, split)`` pairs
    so duplicated group listings with conflicting splits are detectable.
    """

    if isinstance(assignments, Mapping):
        pairs: list[tuple[str, str]] = list(assignments.items())
    else:
        pairs = list(assignments)
    if not pairs:
        raise ValueError("assignments must be non-empty")
    bad_labels = {label for _g, label in pairs if label not in allowed_labels}
    if bad_labels:
        raise ValueError(f"unknown split labels: {sorted(bad_labels)}")
    duplicate_groups: dict[str, list[str]] = {}
    by_split: dict[str, list[str]] = {label: [] for label in allowed_labels}
    seen: dict[str, str] = {}
    for group_id, split in pairs:
        if not group_id or group_id.strip() != group_id:
            raise ValueError("partition group ids must be non-empty opaque strings")
        by_split.setdefault(split, []).append(group_id)
        prior = seen.get(group_id)
        if prior is not None and prior != split:
            duplicate_groups.setdefault(group_id, []).extend([prior, split])
        seen[group_id] = split
    if duplicate_groups:
        raise ValueError(f"partition group split conflict: {duplicate_groups}")
    learn = set(by_split.get("learn", []))
    cal = set(by_split.get("calibration", []))
    test = set(by_split.get("test", []))
    overlap_lc = learn & cal
    overlap_lt = learn & test
    overlap_ct = cal & test
    if overlap_lc or overlap_lt or overlap_ct:
        raise ValueError(
            "template/paraphrase/control leakage: partition group appears in multiple splits "
            f"(learn∩cal={sorted(overlap_lc)}, learn∩test={sorted(overlap_lt)}, cal∩test={sorted(overlap_ct)})"
        )
    return {
        "ok": True,
        "group_counts": {
            label: len(set(by_split.get(label, []))) for label in sorted(allowed_labels)
        },
        "note": "entire scenario groups must move together across splits",
    }


@dataclass(frozen=True, slots=True)
class DevelopmentTrainingProvenance:
    """Pinned development training split, group list, and example digests."""

    split_label: str
    split_digest: str
    group_digest: str
    data_digest: str

    def __post_init__(self) -> None:
        if self.split_label not in DEVELOPMENT_TRAINING_SPLITS:
            raise ValueError(
                "development training provenance requires split_label in "
                f"{sorted(DEVELOPMENT_TRAINING_SPLITS)}"
            )
        _require_digest_hex("split_digest", self.split_digest)
        _require_digest_hex("group_digest", self.group_digest)
        _require_digest_hex("data_digest", self.data_digest)

    def as_manifest(self) -> dict[str, str]:
        return {
            "split_label": self.split_label,
            "split_digest": self.split_digest,
            "group_digest": self.group_digest,
            "data_digest": self.data_digest,
        }


@dataclass(frozen=True, slots=True)
class EmbeddingProvenance:
    """Digest and metadata for precomputed embedding vectors (not TF-IDF)."""

    model_name: str
    model_digest: str
    vector_dim: int
    precompute_manifest_digest: str
    kind: str = BASELINE_KIND_FROZEN_EMBEDDING

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("model_name must be non-empty")
        if self.vector_dim <= 0:
            raise ValueError("vector_dim must be positive")
        _require_digest_hex("model_digest", self.model_digest)
        _require_digest_hex("precompute_manifest_digest", self.precompute_manifest_digest)

    def digest(self) -> str:
        return _sha256_hex(
            canonical_json(
                {
                    "kind": self.kind,
                    "model_name": self.model_name,
                    "model_digest": self.model_digest,
                    "vector_dim": self.vector_dim,
                    "precompute_manifest_digest": self.precompute_manifest_digest,
                }
            )
        )


@dataclass
class TfidfSupervisedBaseline:
    """TF-IDF + logistic classifier; distinct from embedding pipelines."""

    pipeline: Pipeline | None = None
    kind: str = BASELINE_KIND_TFIDF
    fitted_on: str = DEVELOPMENT_FIT_LABEL
    random_state: int = 0
    n_features: int = 0
    training_provenance: DevelopmentTrainingProvenance | None = None

    def fit(
        self,
        texts: Sequence[str],
        labels: Sequence[int],
        *,
        max_features: int = 5000,
        training_provenance: DevelopmentTrainingProvenance,
        training_groups: Sequence[str],
        calibration_groups: Sequence[str] | None = None,
        test_groups: Sequence[str] | None = None,
    ) -> TfidfSupervisedBaseline:
        _assert_development_fit(fitted_on=self.fitted_on, provenance=training_provenance)
        if len(texts) != len(labels):
            raise ValueError("texts and labels length mismatch")
        if not texts:
            raise ValueError("empty training set")
        y = _validate_binary_labels(labels, context="TfidfSupervisedBaseline.fit")
        unique_groups = _normalize_opaque_group_ids(training_groups, context="TfidfSupervisedBaseline.fit")
        group_digest = _digest_training_groups(unique_groups)
        if group_digest != training_provenance.group_digest:
            raise ValueError("training_groups digest does not match training_provenance.group_digest")
        data_digest = _digest_labeled_texts(texts, y)
        if data_digest != training_provenance.data_digest:
            raise ValueError("training data digest does not match training_provenance.data_digest")
        _assert_disjoint_groups(
            unique_groups,
            calibration_groups=calibration_groups,
            test_groups=test_groups,
        )
        pipeline = Pipeline(
            [
                (
                    "tfidf",
                    TfidfVectorizer(
                        max_features=max_features,
                        ngram_range=(1, 2),
                        strip_accents="unicode",
                        lowercase=True,
                        token_pattern=r"(?u)\b\w\w+\b",
                    ),
                ),
                (
                    "clf",
                    LogisticRegression(
                        max_iter=2000,
                        random_state=self.random_state,
                        C=1.0,
                        solver="lbfgs",
                    ),
                ),
            ]
        )
        pipeline.fit(list(texts), y)
        self.pipeline = pipeline
        tfidf: TfidfVectorizer = pipeline.named_steps["tfidf"]
        self.n_features = len(tfidf.vocabulary_)
        self.training_provenance = training_provenance
        return self

    def predict_proba_unsafe(self, observation: Mapping[str, Any]) -> float:
        if self.pipeline is None:
            raise RuntimeError("baseline not fitted")
        text = observation_to_text(observation)
        prob = self.pipeline.predict_proba([text])[0]
        classes = list(self.pipeline.named_steps["clf"].classes_)
        if 1 in classes:
            return float(prob[classes.index(1)])
        return float(prob[-1])

    def manifest_digest(self) -> str:
        if self.pipeline is None or self.training_provenance is None:
            raise RuntimeError("baseline not fitted")
        tfidf: TfidfVectorizer = self.pipeline.named_steps["tfidf"]
        clf: LogisticRegression = self.pipeline.named_steps["clf"]
        payload = {
            "kind": self.kind,
            "fitted_on": self.fitted_on,
            "random_state": self.random_state,
            "n_features": self.n_features,
            "vocabulary_digest": _digest_vocabulary(tfidf.vocabulary_),
            "idf_digest": _digest_ndarray(tfidf.idf_),
            "classes_digest": _digest_ndarray(clf.classes_),
            "coef_digest": _digest_ndarray(clf.coef_),
            "intercept_digest": _digest_ndarray(clf.intercept_),
            "training_provenance": self.training_provenance.as_manifest(),
        }
        return _sha256_hex(canonical_json(payload))


@dataclass
class FrozenEmbeddingClassifier:
    """Logistic head on frozen embedding vectors supplied with provenance."""

    provenance: EmbeddingProvenance
    coefficients: np.ndarray | None = None
    intercept: float = 0.0
    fitted_on: str = DEVELOPMENT_FIT_LABEL
    random_state: int = 0
    training_provenance: DevelopmentTrainingProvenance | None = None

    def fit(
        self,
        embeddings: np.ndarray,
        labels: Sequence[int],
        *,
        training_provenance: DevelopmentTrainingProvenance,
        training_groups: Sequence[str],
        calibration_groups: Sequence[str] | None = None,
        test_groups: Sequence[str] | None = None,
    ) -> FrozenEmbeddingClassifier:
        _assert_development_fit(fitted_on=self.fitted_on, provenance=training_provenance)
        embeddings = np.asarray(embeddings, dtype=float)
        if embeddings.ndim != 2:
            raise ValueError("embeddings must be 2-D")
        if not np.all(np.isfinite(embeddings)):
            raise ValueError("embeddings must be finite")
        if embeddings.shape[0] != len(labels):
            raise ValueError("embeddings/labels length mismatch")
        if embeddings.shape[1] != self.provenance.vector_dim:
            raise ValueError(
                f"expected vector_dim {self.provenance.vector_dim}, got {embeddings.shape[1]}"
            )
        y = _validate_binary_labels(labels, context="FrozenEmbeddingClassifier.fit")
        unique_groups = _normalize_opaque_group_ids(training_groups, context="FrozenEmbeddingClassifier.fit")
        group_digest = _digest_training_groups(unique_groups)
        if group_digest != training_provenance.group_digest:
            raise ValueError("training_groups digest does not match training_provenance.group_digest")
        data_digest = _digest_labeled_embeddings(embeddings, y)
        if data_digest != training_provenance.data_digest:
            raise ValueError("training data digest does not match training_provenance.data_digest")
        _assert_disjoint_groups(
            unique_groups,
            calibration_groups=calibration_groups,
            test_groups=test_groups,
        )
        clf = LogisticRegression(max_iter=2000, random_state=self.random_state, C=1.0, solver="lbfgs")
        clf.fit(embeddings, y)
        self.coefficients = np.asarray(clf.coef_[0], dtype=float)
        self.intercept = float(clf.intercept_[0])
        self.training_provenance = training_provenance
        return self

    def _check_provenance(self, batch_provenance: EmbeddingProvenance) -> None:
        if batch_provenance.digest() != self.provenance.digest():
            raise ValueError("embedding provenance digest mismatch")

    def predict_proba_unsafe(self, embedding: np.ndarray, *, batch_provenance: EmbeddingProvenance) -> float:
        if self.coefficients is None:
            raise RuntimeError("classifier not fitted")
        self._check_provenance(batch_provenance)
        vec = np.asarray(embedding, dtype=float).reshape(-1)
        if vec.shape[0] != self.provenance.vector_dim:
            raise ValueError("embedding dimension mismatch")
        if not np.all(np.isfinite(vec)):
            raise ValueError("embedding must be finite")
        logit = float(np.dot(self.coefficients, vec) + self.intercept)
        return float(expit(logit))

    def predict_proba_batch(
        self, embeddings: np.ndarray, *, batch_provenance: EmbeddingProvenance
    ) -> np.ndarray:
        self._check_provenance(batch_provenance)
        if self.coefficients is None:
            raise RuntimeError("classifier not fitted")
        x = np.asarray(embeddings, dtype=float)
        if x.ndim != 2 or x.shape[1] != self.provenance.vector_dim:
            raise ValueError("invalid embedding batch shape")
        if not np.all(np.isfinite(x)):
            raise ValueError("embeddings must be finite")
        logits = x @ self.coefficients + self.intercept
        return expit(logits)

    def manifest_digest(self) -> str:
        if self.coefficients is None or self.training_provenance is None:
            raise RuntimeError("classifier not fitted")
        payload = {
            "provenance": {
                "model_name": self.provenance.model_name,
                "model_digest": self.provenance.model_digest,
                "vector_dim": self.provenance.vector_dim,
                "precompute_manifest_digest": self.provenance.precompute_manifest_digest,
                "kind": self.provenance.kind,
            },
            "fitted_on": self.fitted_on,
            "random_state": self.random_state,
            "coef_digest": _digest_ndarray(self.coefficients),
            "intercept_digest": _sha256_hex(canonical_json(self.intercept)),
            "training_provenance": self.training_provenance.as_manifest(),
        }
        return _sha256_hex(canonical_json(payload))


def math_is_finite(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))


def _is_valid_review_seconds(value: float | None) -> bool:
    if value is None:
        return False
    if not math_is_finite(value):
        return False
    return value >= 0.0


def _is_non_empty_id(value: str | None) -> bool:
    return value is not None and bool(str(value).strip())


@dataclass(slots=True)
class HumanReviewRecord:
    """One measured human review event (may contain unresolved missing fields)."""

    trajectory_id: str
    record_id: str | None
    reviewer_id: str | None
    decision: str | None
    review_seconds: float | None
    notes: str | None = None

    def is_complete(self) -> bool:
        if not _is_non_empty_id(self.reviewer_id):
            return False
        if self.decision is None or self.decision not in HUMAN_REVIEW_DECISIONS:
            return False
        return _is_valid_review_seconds(self.review_seconds)


@dataclass
class HumanReviewLog:
    """Append-only log of actual reviewer measurements without oracle fill-in."""

    records: list[HumanReviewRecord] = field(default_factory=list)

    def append(self, record: HumanReviewRecord) -> None:
        self.records.append(record)

    def unresolved(self) -> tuple[HumanReviewRecord, ...]:
        return tuple(row for row in self.records if not row.is_complete())

    def total_review_seconds(self) -> float | None:
        """Sum measured seconds; return None if any row has missing or invalid review_seconds."""

        total = 0.0
        for row in self.records:
            if not _is_valid_review_seconds(row.review_seconds):
                return None
            total += float(row.review_seconds)  # type: ignore[arg-type]
        return total

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {
                "trajectory_id": row.trajectory_id,
                "record_id": row.record_id,
                "reviewer_id": row.reviewer_id,
                "decision": row.decision,
                "review_seconds": row.review_seconds,
                "notes": row.notes,
                "complete": row.is_complete(),
            }
            for row in self.records
        ]


__all__ = [
    "BASELINE_KIND_TFIDF",
    "BASELINE_KIND_FROZEN_EMBEDDING",
    "DEVELOPMENT_FIT_LABEL",
    "DEVELOPMENT_TRAINING_SPLITS",
    "FORBIDDEN_POLICY_FEATURE_KEYS",
    "HUMAN_REVIEW_DECISIONS",
    "DevelopmentTrainingProvenance",
    "EmbeddingProvenance",
    "FrozenEmbeddingClassifier",
    "HumanReviewLog",
    "HumanReviewRecord",
    "TfidfSupervisedBaseline",
    "audit_group_split",
    "audit_policy_payload",
    "canonical_json",
    "observation_to_text",
]
