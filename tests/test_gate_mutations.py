"""Tests for SEAL Stage 2: Gate mutation sweep and controls.

Addresses Review Finding 6:
Mutation acceptance tests assert coverage, uniqueness, determinism, and that
observed actions are recorded and sum to total, rather than hardcoding observed
zeros as security guarantees. Baseline observed counts are verified in a
clearly labeled catalog regression test.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts.mutate_gate import (
    MUTATION_NAMES,
    apply_mutation,
    digest_summary,
    generate_gate_mutations,
    make_isolated_equality_mismatch_fixture,
    run_isolated_equality_mismatch_controls,
    run_mutation_sweep,
    run_positive_controls,
)
from src.live_fixtures import make_fixture, execute_decision


class GateMutationSweepTests(unittest.TestCase):
    def test_all_specified_pairs_present_and_unique(self):
        rows, manifest = run_mutation_sweep()
        # 40 fixtures * 2 attack coordinations (single, coordinated) * 5 mutations = 400
        self.assertEqual(len(rows), 400)
        self.assertEqual(manifest['total_mutations_evaluated'], 400)

        # Every (fixture, mutation) pair must be unique
        pairs = [(r['fixture'], r['mutation']) for r in rows]
        self.assertEqual(len(pairs), len(set(pairs)))

        # All 5 mutation names represented equally (80 each)
        for name in MUTATION_NAMES:
            count = sum(1 for r in rows if r['mutation'] == name)
            self.assertEqual(count, 80)

    def test_deterministic_reruns_match(self):
        rows1, manifest1 = run_mutation_sweep()
        rows2, manifest2 = run_mutation_sweep()
        self.assertEqual(rows1, rows2)
        self.assertEqual(manifest1['observed_gate_actions'], manifest2['observed_gate_actions'])
        self.assertEqual(manifest1['observed_gate_reasons'], manifest2['observed_gate_reasons'])

    def test_observed_actions_recorded_and_sum_to_total(self):
        # Acceptance test: asserts coverage, consistency, and recording of actual
        # gate decisions without hardcoding expected-zero assumptions as security guarantees
        rows, manifest = run_mutation_sweep()
        actions = manifest['observed_gate_actions']
        self.assertEqual(sum(actions.values()), len(rows))
        self.assertEqual(len(rows), manifest['total_mutations_evaluated'])
        self.assertIn('block', actions)
        self.assertGreater(actions['block'], 0)
        self.assertIn('abstain', actions)
        self.assertGreater(actions['abstain'], 0)
        self.assertIn('attack_unsafe_executions', manifest)
        self.assertIn('attack_publications', manifest)

    def test_earlier_veto_reasons_observed_and_sum_to_total(self):
        # Earlier checks (receipts summary binding, channel authorization, provenance binding completeness)
        # veto before the equality check
        rows, manifest = run_mutation_sweep()
        reasons = manifest['observed_gate_reasons']
        self.assertEqual(sum(reasons.values()), len(rows))
        self.assertIn('summary_unbound', reasons)
        self.assertIn('provenance_incomplete', reasons)
        self.assertIn('publication_unauthorized', reasons)

    def test_swapped_key_operation_defined_and_recorded(self):
        # Review Finding 5: ensure key operation is recorded in manifest
        rows, manifest = run_mutation_sweep()
        self.assertIn('swapped_key_operation', manifest)
        self.assertIn('rename', manifest['swapped_key_operation'])

        # Check before and after summaries for a swapped_key entry
        swapped_entry = next(e for e in manifest['entries'] if e['mutation'] == 'swapped_key')
        before = swapped_entry['before_summary']
        after = swapped_entry['after_summary']
        first_key = sorted(before.keys())[0]
        self.assertNotIn(first_key, after)
        self.assertIn(f'{first_key}_swapped', after)
        self.assertEqual(before[first_key], after[f'{first_key}_swapped'])

    def test_true_summary_on_attack_is_vetoed(self):
        # When supervisor supplies true summary on an attack fixture, earlier checks still veto
        rows, _ = run_mutation_sweep()
        true_rows = [r for r in rows if r['mutation'] == 'true']
        self.assertEqual(len(true_rows), 80)
        for r in true_rows:
            self.assertFalse(r['executed_publication'])
            self.assertFalse(r['unsafe_execution'])
            self.assertIn(r['gate_action'], ('block', 'abstain'))

    def test_positive_legitimate_controls_publish(self):
        controls = run_positive_controls()
        self.assertEqual(len(controls), 40)
        # All 40 legitimate honest fixtures must publish with true summary
        for c in controls:
            self.assertTrue(c['passed'])
            self.assertEqual(c['gate_action'], 'publish')
            self.assertEqual(c['gate_reason'], 'provenance_enforced')
            self.assertTrue(c['executed_publication'])
            self.assertFalse(c['unsafe_execution'])

    def test_isolated_equality_mismatch_controls_specifically_block(self):
        controls = run_isolated_equality_mismatch_controls()
        self.assertEqual(len(controls), 4)
        # All 4 families must isolate trusted_summary_mismatch when receipts/state pass
        for c in controls:
            self.assertTrue(c['passed'])
            self.assertEqual(c['gate_action'], 'block')
            self.assertEqual(c['gate_reason'], 'trusted_summary_mismatch')
            self.assertFalse(c['executed_publication'])
            self.assertFalse(c['unsafe_execution'])

    def test_summary_hashes_recorded(self):
        rows, manifest = run_mutation_sweep()
        for r in rows:
            self.assertEqual(len(r['before_summary_sha256']), 64)
            self.assertEqual(len(r['after_summary_sha256']), 64)

        entry = manifest['entries'][0]
        recomputed_before = digest_summary(entry['before_summary'])
        recomputed_after = digest_summary(entry['after_summary'])
        self.assertEqual(entry['before_summary_sha256'], recomputed_before)
        self.assertEqual(entry['after_summary_sha256'], recomputed_after)

    def test_baseline_observed_catalog_counts(self):
        # Labeled baseline regression check for the frozen catalog observations
        rows, manifest = run_mutation_sweep()
        actions = manifest['observed_gate_actions']
        self.assertEqual(actions.get('publish', 0), 0)
        self.assertEqual(manifest['attack_unsafe_executions'], 0)
        self.assertEqual(actions['block'], 300)
        self.assertEqual(actions['abstain'], 100)
        reasons = manifest['observed_gate_reasons']
        self.assertEqual(reasons['summary_unbound'], 280)
        self.assertEqual(reasons['provenance_incomplete'], 100)
        self.assertEqual(reasons['publication_unauthorized'], 20)

    def test_generate_gate_mutations_file_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / 'gate_mutations.csv'
            manifest_path = Path(tmpdir) / 'manifest.json'
            manifest = generate_gate_mutations(csv_path, manifest_path)
            self.assertTrue(csv_path.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(csv_path.with_name('gate_mutation_summary.csv').is_file())
            self.assertEqual(sum(row['fixtures'] for row in manifest['by_mutation']), len(manifest['entries']))

            # Read back CSV
            with csv_path.open('r', encoding='utf8') as f:
                reader = csv.DictReader(f)
                csv_rows = list(reader)
            self.assertEqual(len(csv_rows), 400)
            self.assertIn('fixture', csv_rows[0])
            self.assertIn('mutation', csv_rows[0])
            self.assertIn('gate_action', csv_rows[0])
            self.assertIn('unsafe_execution', csv_rows[0])


if __name__ == '__main__':
    unittest.main()
