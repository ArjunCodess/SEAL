"""Offline tests for recovery baselines and human review logging."""
from __future__ import annotations

import hashlib
import unittest

import numpy as np

from src.recovery_baselines import (
    DEVELOPMENT_FIT_LABEL,
    DevelopmentTrainingProvenance,
    EmbeddingProvenance,
    FrozenEmbeddingClassifier,
    HumanReviewLog,
    HumanReviewRecord,
    TfidfSupervisedBaseline,
    audit_group_split,
    audit_policy_payload,
    canonical_json,
    observation_to_text,
)
from src.recovery_fixtures import build_cases, policy_view


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _training_provenance_for_texts(
    texts: list[str],
    labels: list[int],
    groups: list[str],
) -> DevelopmentTrainingProvenance:
    group_digest = _sha256_hex(canonical_json(sorted(set(groups))))
    data_digest = _sha256_hex(
        canonical_json({"texts": texts, "labels": [int(v) for v in labels]})
    )
    return DevelopmentTrainingProvenance(
        split_label="learn",
        split_digest="f" * 64,
        group_digest=group_digest,
        data_digest=data_digest,
    )


def _training_provenance_for_embeddings(
    x: np.ndarray,
    labels: list[int],
    groups: list[str],
) -> DevelopmentTrainingProvenance:
    emb_digest = hashlib.sha256(np.ascontiguousarray(x, dtype=float).tobytes()).hexdigest()
    group_digest = _sha256_hex(canonical_json(sorted(set(groups))))
    data_digest = _sha256_hex(
        canonical_json({"embeddings": emb_digest, "labels": [int(v) for v in labels]})
    )
    return DevelopmentTrainingProvenance(
        split_label="learn",
        split_digest="f" * 64,
        group_digest=group_digest,
        data_digest=data_digest,
    )


class RecoveryBaselinesTests(unittest.TestCase):
    def test_observation_text_excludes_forbidden_fields(self) -> None:
        cases = build_cases()
        obs = policy_view(cases[0])
        text = observation_to_text(obs)
        self.assertIn(cases[0].task[:20], text)
        with self.assertRaises(ValueError):
            audit_policy_payload({**obs, "oracle_details": {}})

    def test_tfidf_fits_and_scores(self) -> None:
        cases = build_cases()
        texts: list[str] = []
        labels: list[int] = []
        groups: list[str] = []
        for idx, case in enumerate(cases):
            obs = policy_view(case)
            texts.append(observation_to_text(obs))
            labels.append(1 if idx % 9 == 0 else 0)
            groups.append(case.scenario_group)
        prov = _training_provenance_for_texts(texts, labels, groups)
        model = TfidfSupervisedBaseline(random_state=0).fit(
            texts,
            labels,
            max_features=200,
            training_provenance=prov,
            training_groups=groups,
        )
        prob = model.predict_proba_unsafe(policy_view(cases[0]))
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)
        self.assertEqual(len(model.manifest_digest()), 64)

    def test_tfidf_manifest_changes_when_vocabulary_changes(self) -> None:
        prov = _training_provenance_for_texts(
            ["alpha beta", "gamma delta"],
            [0, 1],
            ["g.a", "g.b"],
        )
        m1 = TfidfSupervisedBaseline(random_state=0).fit(
            ["alpha beta", "gamma delta"],
            [0, 1],
            max_features=50,
            training_provenance=prov,
            training_groups=["g.a", "g.b"],
        )
        d1 = m1.manifest_digest()
        prov2 = _training_provenance_for_texts(
            ["alpha beta", "gamma epsilon"],
            [0, 1],
            ["g.a", "g.b"],
        )
        m2 = TfidfSupervisedBaseline(random_state=0).fit(
            ["alpha beta", "gamma epsilon"],
            [0, 1],
            max_features=50,
            training_provenance=prov2,
            training_groups=["g.a", "g.b"],
        )
        self.assertNotEqual(d1, m2.manifest_digest())

    def test_tfidf_manifest_changes_when_coefficients_change(self) -> None:
        texts = ["aaa bbb", "ccc ddd", "eee fff", "ggg hhh"]
        labels = [0, 1, 0, 1]
        groups = ["g1", "g2", "g3", "g4"]
        prov = _training_provenance_for_texts(texts, labels, groups)
        m0 = TfidfSupervisedBaseline(random_state=0).fit(
            texts,
            labels,
            max_features=50,
            training_provenance=prov,
            training_groups=groups,
        )
        d0 = m0.manifest_digest()
        m1 = TfidfSupervisedBaseline(random_state=1).fit(
            texts,
            labels,
            max_features=50,
            training_provenance=prov,
            training_groups=groups,
        )
        self.assertNotEqual(d0, m1.manifest_digest())

    def test_nondevelopment_fit_blocked(self) -> None:
        texts = ["one two", "three four"]
        labels = [0, 1]
        with self.assertRaises(ValueError):
            DevelopmentTrainingProvenance(
                split_label="test",
                split_digest="a" * 64,
                group_digest="b" * 64,
                data_digest="c" * 64,
            )
        prov = DevelopmentTrainingProvenance(
            split_label="learn",
            split_digest="a" * 64,
            group_digest=_sha256_hex(canonical_json(["g1", "g2"])),
            data_digest=_training_provenance_for_texts(texts, labels, ["g1", "g2"]).data_digest,
        )
        model = TfidfSupervisedBaseline(fitted_on="sealed_test")
        with self.assertRaises(ValueError):
            model.fit(
                texts,
                labels,
                training_provenance=prov,
                training_groups=["g1", "g2"],
            )

    def test_invalid_class_labels_blocked(self) -> None:
        texts = ["aaa", "bbb", "ccc"]
        prov = _training_provenance_for_texts(texts, [0, 1, 2], ["g1", "g2", "g3"])
        with self.assertRaises(ValueError):
            TfidfSupervisedBaseline().fit(
                texts,
                [0, 1, 2],
                training_provenance=prov,
                training_groups=["g1", "g2", "g3"],
            )
        with self.assertRaises(ValueError):
            TfidfSupervisedBaseline().fit(
                texts,
                [0, 0, 0],
                training_provenance=prov,
                training_groups=["g1", "g2", "g3"],
            )

    def test_group_split_audit_blocks_leakage(self) -> None:
        assignments = {
            "verification.shift-handoff": "learn",
            "verification.tank-gauge": "calibration",
            "verification.batch-yield": "test",
        }
        report = audit_group_split(assignments)
        self.assertTrue(report["ok"])
        with self.assertRaises(ValueError):
            audit_group_split(
                [
                    ("verification.shift-handoff", "learn"),
                    ("code-review.bounded-add", "test"),
                    ("code-review.bounded-add", "learn"),
                ]
            )

    def test_group_split_counts_unique_groups(self) -> None:
        report = audit_group_split(
            [
                ("verification.shift-handoff", "learn"),
                ("verification.shift-handoff", "learn"),
                ("verification.tank-gauge", "learn"),
            ]
        )
        self.assertEqual(report["group_counts"]["learn"], 2)

    def test_frozen_embedding_requires_matching_provenance(self) -> None:
        prov = EmbeddingProvenance(
            model_name="offline-stub",
            model_digest="a" * 64,
            vector_dim=4,
            precompute_manifest_digest="b" * 64,
        )
        other = EmbeddingProvenance(
            model_name="offline-stub",
            model_digest="c" * 64,
            vector_dim=4,
            precompute_manifest_digest="b" * 64,
        )
        x = np.array([[0.0, 1.0, 0.0, 1.0], [1.0, 0.0, 1.0, 0.0], [0.5, 0.5, 0.5, 0.5]], dtype=float)
        y = [0, 1, 0]
        fit_prov = _training_provenance_for_embeddings(x, y, ["g1", "g2", "g3"])
        clf = FrozenEmbeddingClassifier(provenance=prov).fit(
            x,
            y,
            training_provenance=fit_prov,
            training_groups=["g1", "g2", "g3"],
        )
        digest = clf.manifest_digest()
        self.assertEqual(len(digest), 64)
        p = clf.predict_proba_unsafe(x[0], batch_provenance=prov)
        self.assertGreater(p, 0.0)
        with self.assertRaises(ValueError):
            clf.predict_proba_unsafe(x[0], batch_provenance=other)

    def test_frozen_embedding_manifest_changes_with_intercept(self) -> None:
        prov = EmbeddingProvenance(
            model_name="offline-stub",
            model_digest="a" * 64,
            vector_dim=2,
            precompute_manifest_digest="b" * 64,
        )
        x = np.array([[0.0, 1.0], [1.0, 0.0], [0.2, 0.8], [0.9, 0.1]], dtype=float)
        y = [0, 1, 0, 1]
        fit_prov = _training_provenance_for_embeddings(x, y, ["g1", "g2"])
        c0 = FrozenEmbeddingClassifier(provenance=prov, random_state=0).fit(
            x, y, training_provenance=fit_prov, training_groups=["g1", "g2"]
        )
        c1 = FrozenEmbeddingClassifier(provenance=prov, random_state=99).fit(
            x, y, training_provenance=fit_prov, training_groups=["g1", "g2"]
        )
        self.assertNotEqual(c0.manifest_digest(), c1.manifest_digest())

    def test_training_groups_overlap_declared_splits_rejected(self) -> None:
        texts = ["one", "two", "three", "four"]
        labels = [0, 1, 0, 1]
        prov = _training_provenance_for_texts(texts, labels, ["g.learn", "g.other"])
        with self.assertRaises(ValueError):
            TfidfSupervisedBaseline().fit(
                texts,
                labels,
                training_provenance=prov,
                training_groups=["g.learn", "g.other"],
                test_groups=["g.other"],
            )

    def test_human_review_log_leaves_missing_unresolved(self) -> None:
        log = HumanReviewLog()
        log.append(
            HumanReviewRecord(
                trajectory_id="t1",
                record_id="r1",
                reviewer_id="rev_a",
                decision="hold",
                review_seconds=12.5,
            )
        )
        log.append(
            HumanReviewRecord(
                trajectory_id="t2",
                record_id="r2",
                reviewer_id=None,
                decision="hold",
                review_seconds=None,
            )
        )
        self.assertEqual(len(log.unresolved()), 1)
        self.assertIsNone(log.total_review_seconds())
        self.assertFalse(log.unresolved()[0].is_complete())

    def test_human_review_rejects_empty_reviewer_and_bad_decision(self) -> None:
        row = HumanReviewRecord(
            trajectory_id="t",
            record_id="r",
            reviewer_id="   ",
            decision="hold",
            review_seconds=1.0,
        )
        self.assertFalse(row.is_complete())
        row2 = HumanReviewRecord(
            trajectory_id="t",
            record_id="r",
            reviewer_id="rev",
            decision="oracle_override",
            review_seconds=1.0,
        )
        self.assertFalse(row2.is_complete())

    def test_human_review_total_rejects_negative_and_nan(self) -> None:
        log = HumanReviewLog()
        log.append(
            HumanReviewRecord(
                trajectory_id="t1",
                record_id="r1",
                reviewer_id="rev",
                decision="hold",
                review_seconds=5.0,
            )
        )
        log.append(
            HumanReviewRecord(
                trajectory_id="t2",
                record_id="r2",
                reviewer_id="rev",
                decision="hold",
                review_seconds=-0.1,
            )
        )
        self.assertIsNone(log.total_review_seconds())
        log2 = HumanReviewLog()
        log2.append(
            HumanReviewRecord(
                trajectory_id="t1",
                record_id="r1",
                reviewer_id="rev",
                decision="hold",
                review_seconds=5.0,
            )
        )
        log2.append(
            HumanReviewRecord(
                trajectory_id="t2",
                record_id="r2",
                reviewer_id="rev",
                decision="hold",
                review_seconds=float("nan"),
            )
        )
        total = log2.total_review_seconds()
        self.assertIsNone(total)
        self.assertFalse(isinstance(total, float) and total != total)


if __name__ == "__main__":
    unittest.main()
