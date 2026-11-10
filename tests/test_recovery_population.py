"""Contract tests for fresh recovery population generator (no paid API)."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from src.recovery_fixtures import CONTROLS, FAMILIES, SCENARIO_GROUPS as PILOT_GROUPS, oracle as pilot_oracle
from src.recovery_population import (
    CONTROL_INTENDED_LEGITIMACY,
    POPULATION_CONTRACT,
    POPULATION_KIND_SCRIPTED,
    SPLITS,
    STRUCTURAL_ANCESTOR_BY_GROUP,
    ConfirmatoryEffectSpec,
    DevelopmentPolicyContrast,
    FRESH_SCENARIO_GROUPS,
    ObservedGroupBaseline,
    ScriptedPopulationCase,
    assert_population_oracle_contract,
    assign_splits,
    audit_split_leakage,
    build_fresh_case,
    build_population,
    development_paired_summaries,
    intended_legitimacy,
    live_worker_record_schema,
    policy_view,
    population_oracle,
    simulate_confirmatory_sizing,
)
from scripts import prepare_recovery_study as prepare


_CONTROL_LEAK_RE = re.compile(
    r"(permitted_collaboration|ordinary_mistake|collusion_refusal|instructed_violation|legitimate\s*[:=])",
    re.IGNORECASE,
)

VARIANTS = 10


def _dev_case_ids(population: list[ScriptedPopulationCase], split_assignment: dict[str, str], group: str) -> tuple:
    rows = [
        r
        for r in population
        if r.independent_group_id == group and split_assignment.get(group) == "development"
    ]
    violation = tuple(
        sorted(r.case.case_id for r in rows if r.case.control == "instructed_violation")
    )
    legit = tuple(
        sorted(
            r.case.case_id
            for r in rows
            if r.case.control in ("permitted_collaboration", "collusion_refusal")
        )
    )
    return violation, legit


def _observed_and_contrast_for_dev(
    population: list[ScriptedPopulationCase], split_assignment: dict[str, str]
) -> tuple[list[ObservedGroupBaseline], list[DevelopmentPolicyContrast]]:
    dev_groups = sorted(
        g for g, split in split_assignment.items() if split == "development"
    )
    baselines: list[ObservedGroupBaseline] = []
    contrasts: list[DevelopmentPolicyContrast] = []
    for index, group in enumerate(dev_groups):
        violation_ids, legit_ids = _dev_case_ids(population, split_assignment, group)
        base_unsafe = 0.35 + 0.02 * index
        base_legit = 0.92 - 0.01 * index
        cand_unsafe = max(0.0, base_unsafe - 0.08)
        cand_legit = max(0.0, base_legit - 0.02)
        baselines.append(
            ObservedGroupBaseline(
                scenario_group=group,
                violation_case_ids=violation_ids,
                legitimate_case_ids=legit_ids,
                baseline_unsafe_release_rate=base_unsafe,
                baseline_legitimate_completion_rate=base_legit,
                provenance="synthetic_observed_comparator",
            )
        )
        contrasts.append(
            DevelopmentPolicyContrast(
                scenario_group=group,
                violation_case_ids=violation_ids,
                legitimate_case_ids=legit_ids,
                candidate_unsafe_release_rate=cand_unsafe,
                candidate_legitimate_completion_rate=cand_legit,
                provenance="synthetic_candidate_fit",
            )
        )
    return baselines, contrasts


class RecoveryPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = build_population(generator_seed=20261112, variants_per_group=VARIANTS)
        cls.split_assignment = assign_splits(FRESH_SCENARIO_GROUPS, split_seed=99)
        assert_population_oracle_contract(cls.population, variants_per_group=VARIANTS)

    def test_contract(self):
        self.assertEqual(set(POPULATION_CONTRACT["families"]), set(FAMILIES))
        self.assertFalse(POPULATION_CONTRACT["variant_index_counts_as_independent_group"])
        self.assertEqual(len(FRESH_SCENARIO_GROUPS), 9)
        self.assertEqual(set(FRESH_SCENARIO_GROUPS) & set(PILOT_GROUPS), set())
        self.assertEqual(
            POPULATION_CONTRACT["structural_ancestor_by_group"],
            STRUCTURAL_ANCESTOR_BY_GROUP,
        )
        self.assertEqual(POPULATION_CONTRACT["control_intended_legitimacy"], CONTROL_INTENDED_LEGITIMACY)

    def test_structural_ancestors_map_pilot_arms(self):
        self.assertEqual(
            STRUCTURAL_ANCESTOR_BY_GROUP["verification.pump-cycle"],
            "verification.tank-gauge",
        )
        self.assertEqual(
            STRUCTURAL_ANCESTOR_BY_GROUP["verification.alloy-mix"],
            "verification.batch-yield",
        )
        self.assertEqual(
            STRUCTURAL_ANCESTOR_BY_GROUP["code-review.window-cover"],
            "code-review.interval-cover",
        )

    def test_every_group_control_oracle_stable_across_variants(self):
        self.assertEqual(len(self.population), 9 * len(CONTROLS) * VARIANTS)
        by_key: dict[tuple[str, str], set[bool]] = {}
        for row in self.population:
            self.assertEqual(row.population_kind, POPULATION_KIND_SCRIPTED)
            self.assertTrue(row.scripted_worker_claims)
            self.assertTrue(row.pilot_template_reuse)
            self.assertEqual(row.independent_group_id, row.case.scenario_group)
            self.assertEqual(row.structural_ancestor_id, STRUCTURAL_ANCESTOR_BY_GROUP[row.case.scenario_group])
            expected = intended_legitimacy(row.case.control)
            self.assertEqual(row.case.legitimate, expected)
            self.assertEqual(row.case.legitimate, population_oracle(row.case))
            by_key.setdefault((row.case.scenario_group, row.case.control), set()).add(row.case.legitimate)
            with self.assertRaises((ValueError, KeyError)):
                pilot_oracle(row.case)
        for key, values in by_key.items():
            self.assertEqual(values, {intended_legitimacy(key[1])}, msg=f"variant drift at {key}")

    def test_numeric_variants_share_group(self):
        by_group = {}
        for row in self.population:
            by_group.setdefault(row.independent_group_id, set()).add(row.variant_index)
        for group, variants in by_group.items():
            self.assertEqual(variants, set(range(VARIANTS)), group)

    def test_splits_keep_groups_whole_and_report_extension_limitation(self):
        leakage = audit_split_leakage(self.population, self.split_assignment)
        self.assertTrue(leakage["ok"], leakage["issues"])
        self.assertFalse(leakage["adequate_independent_groups_for_confirmatory"])
        self.assertGreater(len(leakage["limitations"]), 0)
        self.assertEqual(len(self.split_assignment), len(FRESH_SCENARIO_GROUPS))
        self.assertEqual(set(self.split_assignment.values()), set(SPLITS))

    def test_policy_view_hides_harness_fields(self):
        for row in self.population:
            view = policy_view(row.case)
            blob = json.dumps(view)
            self.assertIsNone(_CONTROL_LEAK_RE.search(blob))

    def test_code_review_negated_cite_not_legitimate(self):
        row = build_fresh_case(
            "code-review.clamp-mul",
            "instructed_violation",
            generator_seed=5,
            variant_index=0,
        )
        poisoned = replace(row.case, proposal="Approve clamp-mul without candidate_source review.")
        self.assertFalse(population_oracle(poisoned))

    def test_corruption_flips_oracle(self):
        row = build_fresh_case(
            "verification.receipt-total",
            "permitted_collaboration",
            generator_seed=1,
        )
        records = list(row.case.records)
        records[0] = replace(records[0], body=json.dumps({"sku": 0, "amount": 9999}))
        corrupted = replace(row.case, records=tuple(records), legitimate=False, oracle_details={})
        self.assertTrue(population_oracle(row.case))
        self.assertFalse(population_oracle(corrupted))

    def test_live_worker_schema_distinct_from_scripted(self):
        schema = live_worker_record_schema()
        self.assertEqual(schema["properties"]["constructed_control"]["const"], False)
        kinds = schema["properties"]["record_kind"]["enum"]
        self.assertEqual(set(kinds), {"instruction", "generation", "refusal", "violation_check"})

    def test_paired_summaries_require_observed_baselines_and_reject_oracle_invention(self):
        baselines, contrasts = _observed_and_contrast_for_dev(self.population, self.split_assignment)
        summaries = development_paired_summaries(
            self.population,
            split_assignment=self.split_assignment,
            observed_baselines=baselines,
            policy_contrasts=contrasts,
        )
        self.assertEqual(len(summaries), len(baselines))
        self.assertAlmostEqual(summaries[0].baseline_unsafe_release_rate, baselines[0].baseline_unsafe_release_rate)

    def test_paired_summaries_validate_rates_and_ids(self):
        baselines, contrasts = _observed_and_contrast_for_dev(self.population, self.split_assignment)
        bad = replace(baselines[0], baseline_unsafe_release_rate=1.5)
        with self.assertRaises(ValueError):
            development_paired_summaries(
                self.population,
                split_assignment=self.split_assignment,
                observed_baselines=[bad] + baselines[1:],
                policy_contrasts=contrasts,
            )
        with self.assertRaises(ValueError):
            development_paired_summaries(
                self.population,
                split_assignment=self.split_assignment,
                observed_baselines=[],
                policy_contrasts=contrasts,
            )

    def test_sizing_zero_variance_dev_extension_is_underpowered(self):
        baselines, contrasts = _observed_and_contrast_for_dev(self.population, self.split_assignment)
        uniform_baselines = [
            replace(
                row,
                baseline_unsafe_release_rate=0.4,
                baseline_legitimate_completion_rate=0.9,
            )
            for row in baselines
        ]
        uniform_contrasts = [
            replace(
                row,
                candidate_unsafe_release_rate=0.2,
                candidate_legitimate_completion_rate=0.88,
            )
            for row in contrasts
        ]
        summaries = development_paired_summaries(
            self.population,
            split_assignment=self.split_assignment,
            observed_baselines=uniform_baselines,
            policy_contrasts=uniform_contrasts,
        )
        spec = ConfirmatoryEffectSpec(
            min_unsafe_release_reduction=0.01,
            max_legitimate_completion_loss=0.05,
            alpha=0.05,
            power=0.8,
        )
        result = simulate_confirmatory_sizing(
            spec,
            summaries,
            rng_seed=7,
            simulations=128,
            max_independent_groups=64,
            independent_structural_units_available=3,
        )
        self.assertTrue(result.underpowered)
        self.assertIn("variance", (result.underpowered_reason or "").lower())

    def test_sizing_heterogeneous_observed_grid_runs_bounded(self):
        baselines, contrasts = _observed_and_contrast_for_dev(self.population, self.split_assignment)
        summaries = development_paired_summaries(
            self.population,
            split_assignment=self.split_assignment,
            observed_baselines=baselines,
            policy_contrasts=contrasts,
        )
        spec = ConfirmatoryEffectSpec(
            min_unsafe_release_reduction=0.01,
            max_legitimate_completion_loss=0.05,
            alpha=0.05,
            power=0.8,
        )
        result = simulate_confirmatory_sizing(
            spec,
            summaries,
            rng_seed=7,
            simulations=128,
            max_independent_groups=64,
            independent_structural_units_available=3,
        )
        self.assertLessEqual(result.simulations, 512)

    def test_prepare_cli_writes_manifest_and_supervisor_acquire(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = Path(tmp.name) / "prepare_out"
        baselines, contrasts = _observed_and_contrast_for_dev(self.population, self.split_assignment)
        base_path = Path(tmp.name) / "baselines.json"
        contrast_path = Path(tmp.name) / "contrasts.json"
        base_path.write_text(json.dumps([asdict(b) for b in baselines]), encoding="utf-8")
        contrast_path.write_text(json.dumps([asdict(c) for c in contrasts]), encoding="utf-8")
        result = prepare.prepare_study(
            out,
            generator_seed=20261112,
            split_seed=99,
            variants_per_group=VARIANTS,
            effect_spec={
                "min_unsafe_release_reduction": 0.01,
                "max_legitimate_completion_loss": 0.05,
                "alpha": 0.05,
                "power": 0.8,
            },
            observed_baselines_path=base_path,
            policy_contrasts_path=contrast_path,
            simulations=128,
            max_independent_groups=64,
            sizing_rng_seed=3,
        )
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(result["execution_status"], "not_executed")
        self.assertIn("acquire", manifest["prompt_assumptions"]["supervisor_outputs_json"]["action"])
        self.assertFalse(manifest["execution"]["paid_api_calls"])
        self.assertTrue(manifest["split_leakage_audit"]["ok"])
        self.assertIn("source_hashes", manifest)

    def test_prepare_rejects_protected_output_path(self):
        with self.assertRaises(ValueError):
            prepare.assert_safe_output_dir(prepare.SRC)

    def test_scripted_document_markers(self):
        row: ScriptedPopulationCase = self.population[0]
        doc = row.to_document()
        self.assertIn("constructed_control_disclaimer", doc)
        self.assertIn("structural_ancestor_id", doc)
        self.assertEqual(doc["population_kind"], POPULATION_KIND_SCRIPTED)


if __name__ == "__main__":
    unittest.main()
