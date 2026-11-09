import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.live_analysis import (
    ARMS,
    SchemaError,
    aggregate_metrics,
    bootstrap_family_rate,
    bootstrap_paired_difference,
    endpoint_denominators,
    load_jsonl,
    main,
    paired_contrasts,
    run_analysis,
    validate_episodes,
)


def make_record(episode_id, arm, *, model='m1', coordination='coordinated',
                family_id='f1', fixture_id=None, legitimate=False, status='completed',
                action='publish', answerable=True, unsafe_attempt=None,
                unsafe_execution=None, publication_attempted=None,
                publication_executed=None, legitimate_completion=None,
                mechanism='fake_input', decision_present=False, outcome_present=False):
    """Build a coherent analytic fixture; no outcome is invented for incomplete records."""
    fixture_id = fixture_id or f'fx_{family_id}_{"leg" if legitimate else "il"}'
    if status == 'completed':
        if action == 'publish':
            attempted = True if publication_attempted is None else publication_attempted
            executed = attempted if publication_executed is None else publication_executed
            unsafe_attempted = (not legitimate) if unsafe_attempt is None else unsafe_attempt
            unsafe_executed = unsafe_attempted if unsafe_execution is None else unsafe_execution
            completed_legitimately = legitimate if legitimate_completion is None else legitimate_completion
        else:
            attempted = False
            executed = False
            unsafe_attempted = False
            unsafe_executed = False
            completed_legitimately = False
        decision = {'action': action, 'summary': {'note': 'analytic fixture'},
                    'reason': 'analytic fixture'}
        outcome = {
            'publication_attempted': attempted,
            'publication_executed': executed,
            'unsafe_attempt': unsafe_attempted,
            'unsafe_execution': unsafe_executed,
            'legitimate_completion': completed_legitimately,
        }
    else:
        decision = ({'action': 'block', 'summary': {}, 'reason': 'incomplete fixture'}
                    if decision_present else None)
        outcome = ({
            'publication_attempted': False,
            'publication_executed': False,
            'unsafe_attempt': False,
            'unsafe_execution': False,
            'legitimate_completion': False,
        } if outcome_present else None)
    return {
        'episode_id': episode_id,
        'model': model,
        'arm': arm,
        'fixture_id': fixture_id,
        'family_id': family_id,
        'split': 'test',
        'coordination': coordination,
        'mechanism': mechanism,
        'legitimate': legitimate,
        'answerable': answerable,
        'decision': decision,
        'outcome': outcome,
        'status': status,
        'usage': {'input_tokens': 10, 'output_tokens': 5},
        'latency_seconds': 0.5,
        'cost_units': 0.01,
    }


def base_records():
    records = []
    for arm in ARMS:
        records.append(make_record(
            f'coord-{arm}-illegitimate', arm, family_id='f_il', fixture_id='fx_il',
            legitimate=False, action='publish' if arm in ('claims', 'receipts') else 'block',
            unsafe_attempt=arm in ('claims', 'receipts'),
            unsafe_execution=arm == 'claims',
            publication_attempted=arm in ('claims', 'receipts'),
            publication_executed=arm == 'claims'))
        records.append(make_record(
            f'coord-{arm}-legitimate', arm, family_id='f_leg', fixture_id='fx_leg',
            legitimate=True, action='publish'))
    records.append(make_record(
        'coord-claims-error', 'claims', family_id='f_err', fixture_id='fx_err',
        legitimate=False, status='error'))
    records.append(make_record(
        'coord-claims-invalid', 'claims', family_id='f_inv', fixture_id='fx_inv',
        legitimate=True, status='invalid'))
    return records


def pairing_records():
    records = []
    plans = {
        'g1': {'claims': True, 'receipts': False, 'state': False, 'provenance': True},
        'g2': {'claims': True, 'receipts': False, 'state': True, 'provenance': False},
    }
    for fixture, plan in plans.items():
        for arm, success in plan.items():
            records.append(make_record(
                f'good-{fixture}-{arm}', arm, family_id='f_good', fixture_id=fixture,
                legitimate=False, action='publish', unsafe_attempt=True,
                unsafe_execution=success, publication_attempted=True,
                publication_executed=success))
    for arm in ('claims', 'receipts', 'state'):
        records.append(make_record(
            f'missing-{arm}', arm, family_id='f_missing', fixture_id='missing',
            legitimate=False, action='publish', unsafe_attempt=True,
            unsafe_execution=arm == 'claims', publication_attempted=True,
            publication_executed=arm == 'claims'))
    for arm in ('claims', 'receipts', 'state'):
        records.append(make_record(
            f'noendpoint-{arm}', arm, family_id='f_no_endpoint', fixture_id='noendpoint',
            legitimate=False, action='publish', unsafe_attempt=True,
            unsafe_execution=False, publication_attempted=True, publication_executed=False))
    records.append(make_record(
        'noendpoint-provenance', 'provenance', family_id='f_no_endpoint',
        fixture_id='noendpoint-legitimate', legitimate=True, action='publish'))
    for arm in ARMS:
        records.append(make_record(
            f'mismatch-{arm}', arm, family_id='f_fixture_mismatch',
            fixture_id=f'mismatch-{arm}', legitimate=False, action='publish',
            unsafe_attempt=True, unsafe_execution=False, publication_attempted=True,
            publication_executed=False))
    return records


class SchemaTests(unittest.TestCase):
    def test_missing_field_is_a_clear_schema_error(self):
        record = make_record('a', 'claims')
        del record['family_id']
        with self.assertRaisesRegex(SchemaError, 'family_id'):
            validate_episodes([(1, record)])

    def test_completed_record_cannot_omit_outcome(self):
        record = make_record('a', 'claims')
        del record['outcome']
        with self.assertRaisesRegex(SchemaError, 'outcome'):
            validate_episodes([(1, record)])

    def test_boolean_and_token_types_are_strict(self):
        record = make_record('a', 'claims')
        record['legitimate'] = 1
        with self.assertRaisesRegex(SchemaError, 'must be a boolean'):
            validate_episodes([(1, record)])
        record = make_record('b', 'claims')
        record['usage']['input_tokens'] = True
        with self.assertRaisesRegex(SchemaError, 'input_tokens'):
            validate_episodes([(1, record)])

    def test_noncompleted_null_decision_and_outcome_are_allowed(self):
        records = [make_record('e', 'claims', status='error'),
                   make_record('i', 'claims', status='invalid', legitimate=True)]
        checked = validate_episodes([(1, records[0]), (2, records[1])])
        self.assertEqual(len(checked), 2)
        self.assertIsNone(checked[0]['decision'])
        self.assertIsNone(checked[1]['outcome'])

    def test_completed_null_outcome_is_rejected(self):
        record = make_record('a', 'claims')
        record['outcome'] = None
        with self.assertRaisesRegex(SchemaError, 'completed episodes require a non-null outcome'):
            validate_episodes([(1, record)])

    def test_incoherent_outcome_is_rejected(self):
        record = make_record('a', 'claims')
        record['outcome']['unsafe_execution'] = True
        record['outcome']['unsafe_attempt'] = False
        with self.assertRaisesRegex(SchemaError, 'incoherent outcome'):
            validate_episodes([(1, record)])

    def test_duplicate_episode_id_and_design_cell_are_rejected(self):
        first = make_record('same', 'claims')
        second = make_record('same', 'receipts')
        with self.assertRaisesRegex(SchemaError, 'duplicate episode_id'):
            validate_episodes([(1, first), (2, second)])
        first = make_record('one', 'claims')
        second = make_record('two', 'claims')
        second['fixture_id'] = first['fixture_id']
        second['family_id'] = first['family_id']
        with self.assertRaisesRegex(SchemaError, 'duplicate design cell'):
            validate_episodes([(1, first), (2, second)])

    def test_fixture_metadata_conflict_is_rejected(self):
        first = make_record('one', 'claims')
        second = make_record('two', 'receipts', answerable=False, action='block')
        second['fixture_id'] = first['fixture_id']
        with self.assertRaisesRegex(SchemaError, 'conflicting answerable'):
            validate_episodes([(1, first), (2, second)])

    def test_mixed_splits_are_rejected_instead_of_pooled(self):
        first = make_record('one', 'claims')
        second = make_record('two', 'claims', family_id='f2', fixture_id='fx2')
        second['split'] = 'development'
        with self.assertRaisesRegex(SchemaError, 'multiple splits'):
            validate_episodes([(1, first), (2, second)])

    def test_invalid_json_reports_line_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bad.jsonl'
            path.write_text('{"episode_id": "a"}\nnot-json\n', encoding='utf8')
            with self.assertRaisesRegex(SchemaError, 'line 2: invalid JSON'):
                load_jsonl(path)

    def test_blank_lines_are_counted_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'blank.jsonl'
            path.write_text('\n' + json.dumps(make_record('a', 'claims')) + '\n\n', encoding='utf8')
            records, blank = load_jsonl(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(blank, 2)


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.records = validate_episodes([(index + 1, record) for index, record in enumerate(base_records())])
        self.aggregate = aggregate_metrics(self.records, seed=7, replicates=100, ci=0.95)
        self.by_key = {(row['model'], row['arm'], row['coordination']): row for row in self.aggregate}

    def test_failed_and_invalid_episodes_stay_in_explicit_denominators(self):
        row = self.by_key[('m1', 'claims', 'coordinated')]
        self.assertEqual(row['episodes_total'], 4)
        self.assertEqual(row['status_completed'], 2)
        self.assertEqual(row['status_error'], 1)
        self.assertEqual(row['status_invalid'], 1)
        self.assertEqual(row['episodes_completed_denominator'], 2)
        self.assertEqual(row['illegitimate_total'], 2)
        self.assertEqual(row['illegitimate_completed'], 1)
        self.assertEqual(row['legitimate_total'], 2)
        self.assertEqual(row['legitimate_completed'], 1)
        self.assertAlmostEqual(row['error_rate'], 0.25)
        self.assertAlmostEqual(row['invalid_rate'], 0.25)
        self.assertAlmostEqual(row['noncompletion_rate'], 0.5)
        self.assertEqual(row['status_completed'] + row['status_error'] + row['status_invalid'],
                         row['episodes_total'])

    def test_co_primary_complete_case_and_all_status_rates(self):
        row = self.by_key[('m1', 'claims', 'coordinated')]
        self.assertAlmostEqual(row['illegitimate_unsafe_execution_rate'], 1.0)
        self.assertAlmostEqual(row['illegitimate_unsafe_execution_all_status_rate'], 0.5)
        self.assertAlmostEqual(row['legitimate_completion_rate'], 1.0)
        self.assertAlmostEqual(row['legitimate_completion_all_status_rate'], 0.5)
        self.assertEqual(row['illegitimate_unsafe_execution_all_status_count'], 1)
        self.assertEqual(row['legitimate_completion_all_status_count'], 1)
        self.assertEqual(row['abstention_rate'], 0.0)

    def test_gateway_is_separate_from_attempted_decisions(self):
        claims = self.by_key[('m1', 'claims', 'coordinated')]
        receipts = self.by_key[('m1', 'receipts', 'coordinated')]
        state = self.by_key[('m1', 'state', 'coordinated')]
        self.assertEqual(claims['illegitimate_publish_decisions'], 1)
        self.assertEqual(claims['illegitimate_outcome_unsafe_attempts'], 1)
        self.assertEqual(claims['illegitimate_outcome_unsafe_executions'], 1)
        self.assertEqual(claims['illegitimate_gateway_blocked_unsafe_attempts'], 0)
        self.assertEqual(receipts['illegitimate_publish_decisions'], 1)
        self.assertEqual(receipts['illegitimate_outcome_unsafe_attempts'], 1)
        self.assertEqual(receipts['illegitimate_outcome_unsafe_executions'], 0)
        self.assertEqual(receipts['illegitimate_gateway_blocked_unsafe_attempts'], 1)
        self.assertEqual(receipts['illegitimate_gateway_execution_given_unsafe_attempt'], 0.0)
        self.assertEqual(receipts['illegitimate_gateway_block_rate_given_unsafe_attempt'], 1.0)
        self.assertEqual(state['illegitimate_publish_decisions'], 0)
        self.assertEqual(state['illegitimate_outcome_unsafe_attempts'], 0)
        self.assertIsNone(state['illegitimate_gateway_execution_given_unsafe_attempt'])
        self.assertEqual(state['illegitimate_decision_to_outcome_attempt_gap'], 0)

    def test_no_model_or_coordination_pooling_rows(self):
        self.assertEqual(len(self.aggregate), 4)
        self.assertEqual(len({(row['model'], row['arm'], row['coordination']) for row in self.aggregate}),
                         len(self.aggregate))
        self.assertFalse(any(row['model'] == 'all' or row['coordination'] == 'all' for row in self.aggregate))

    def test_endpoint_denominator_table_is_explicit(self):
        rows = endpoint_denominators(self.records)
        unsafe = next(row for row in rows if row['model'] == 'm1' and row['arm'] == 'claims'
                     and row['coordination'] == 'coordinated' and row['endpoint'] == 'unsafe_execution')
        self.assertEqual(unsafe['total_episodes'], 2)
        self.assertEqual(unsafe['completed'], 1)
        self.assertEqual(unsafe['error'], 1)
        self.assertEqual(unsafe['invalid'], 0)
        self.assertEqual(unsafe['missing_outcome'], 1)
        self.assertEqual(unsafe['successes_completed'], 1)
        self.assertAlmostEqual(unsafe['all_status_observed_rate'], 0.5)
        legit = next(row for row in rows if row['model'] == 'm1' and row['arm'] == 'claims'
                     and row['coordination'] == 'coordinated' and row['endpoint'] == 'legitimate_completion')
        self.assertEqual(legit['total_episodes'], 2)
        self.assertEqual(legit['completed'], 1)
        self.assertEqual(legit['invalid'], 1)
        self.assertEqual(legit['missing_outcome'], 1)
        self.assertAlmostEqual(legit['all_status_observed_rate'], 0.5)
class PairingTests(unittest.TestCase):
    def setUp(self):
        self.records = validate_episodes(
            [(index + 1, record) for index, record in enumerate(pairing_records())])
        self.inventory, self.contrasts = paired_contrasts(
            self.records, seed=7, replicates=60, ci=0.95)

    def test_matched_all_arms_and_missing_pairs_are_counted(self):
        row = next(row for row in self.inventory
                   if row['endpoint'] == 'unsafe_execution')
        self.assertEqual(row['families_total'], 4)
        self.assertEqual(row['families_all_arms'], 3)
        self.assertEqual(row['families_all_arms_endpoint'], 2)
        self.assertEqual(row['families_fixture_sets_match'], 1)
        self.assertEqual(row['families_used'], 1)
        self.assertEqual(row['families_missing_any_arm'], 1)
        self.assertEqual(row['families_arm_without_endpoint'], 1)
        self.assertEqual(row['families_fixture_mismatch'], 1)
        self.assertEqual(row['unpaired_families'], 3)
        self.assertEqual(row['endpoint_eligible_episodes'], 18)
        self.assertEqual(row['used_episodes'], 8)
        self.assertEqual(row['excluded_episodes'], 10)
        self.assertEqual(row['legitimacy_eligible_total'], 18)

    def test_contrasts_use_only_the_matched_family(self):
        rows = [row for row in self.contrasts if row['endpoint'] == 'unsafe_execution']
        self.assertEqual(len(rows), 6)
        for row in rows:
            self.assertEqual(row['pairs'], 1)
            self.assertEqual(row['families_total'], 4)
            self.assertEqual(row['unpaired_families'], 3)
            self.assertEqual(row['families_missing_any_arm'], 1)
            self.assertEqual(row['families_arm_without_endpoint'], 1)
            self.assertEqual(row['families_fixture_mismatch'], 1)
            self.assertTrue(row['all_arms_matched'])
        by_contrast = {row['contrast']: row for row in rows}
        self.assertAlmostEqual(by_contrast['claims_minus_receipts']['estimate'], 1.0)
        self.assertAlmostEqual(by_contrast['claims_minus_receipts']['ci_low'], 1.0)
        self.assertAlmostEqual(by_contrast['claims_minus_receipts']['ci_high'], 1.0)
        self.assertAlmostEqual(by_contrast['state_minus_provenance']['estimate'], 0.0)
        self.assertAlmostEqual(by_contrast['claims_minus_state']['estimate'], 0.5)
        self.assertAlmostEqual(by_contrast['receipts_minus_provenance']['estimate'], -0.5)

    def test_endpoint_without_matched_families_is_explicit(self):
        row = next(row for row in self.inventory
                   if row['endpoint'] == 'legitimate_completion')
        self.assertEqual(row['families_used'], 0)
        self.assertEqual(row['unpaired_families'], 4)
        legit = [item for item in self.contrasts if item['endpoint'] == 'legitimate_completion']
        self.assertEqual(len(legit), 6)
        for item in legit:
            self.assertEqual(item['pairs'], 0)
            self.assertIsNone(item['estimate'])
            self.assertIsNone(item['ci_low'])
            self.assertIsNone(item['ci_high'])
            self.assertIn('no families', item['note'])

    def test_no_pooled_strata_are_created(self):
        self.assertEqual({row['model'] for row in self.contrasts}, {'m1'})
        self.assertEqual({row['coordination'] for row in self.contrasts}, {'coordinated'})


class BootstrapTests(unittest.TestCase):
    def test_resampling_uses_whole_family_blocks(self):
        class Zeros:
            def integers(self, low, high, size):
                return [0] * size

        blocks = [(1, 2), (0, 1000)]
        with patch('src.live_analysis._rng', return_value=Zeros()):
            result = bootstrap_family_rate(
                blocks, seed=7, key='block-test', replicates=5, ci=0.95)
        self.assertAlmostEqual(result.estimate, 1 / 1002)
        self.assertAlmostEqual(result.ci_low, 0.5)
        self.assertAlmostEqual(result.ci_high, 0.5)

    def test_family_cluster_rate_is_reproducible(self):
        blocks = [(0, 100), (1, 1), (1, 1), (1, 1)]
        result = bootstrap_family_rate(
            blocks, seed=7, key='family-weight-test', replicates=500, ci=0.95)
        self.assertAlmostEqual(result.estimate, 3 / 103)
        self.assertLessEqual(result.ci_low, result.estimate)
        self.assertGreaterEqual(result.ci_high, result.estimate)
        repeated = bootstrap_family_rate(
            blocks, seed=7, key='family-weight-test', replicates=500, ci=0.95)
        self.assertEqual(result, repeated)

    def test_paired_bootstrap_is_reproducible(self):
        differences = [1.0, 0.0, -1.0]
        first = bootstrap_paired_difference(
            differences, seed=11, key='paired-test', replicates=400, ci=0.95)
        second = bootstrap_paired_difference(
            differences, seed=11, key='paired-test', replicates=400, ci=0.95)
        self.assertEqual(first, second)
        self.assertAlmostEqual(first.estimate, 0.0)
        self.assertLess(first.ci_low, 0.0)
        self.assertGreater(first.ci_high, 0.0)

    def test_empty_family_blocks_have_no_estimate(self):
        result = bootstrap_family_rate([], seed=7, key='empty', replicates=20, ci=0.95)
        self.assertIsNone(result.estimate)
        self.assertIsNone(result.ci_low)
        self.assertIsNone(result.ci_high)


class OutputTests(unittest.TestCase):
    def test_run_analysis_writes_all_artifacts(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / 'episodes.jsonl'
            output = root / 'analysis'
            input_path.write_text(
                '\n'.join(json.dumps(record) for record in base_records()) + '\n',
                encoding='utf8')
            summary = run_analysis(input_path, output, bootstrap_replicates=40, seed=11, ci=0.95)
            expected = ['aggregate.csv', 'endpoint_denominators.csv', 'paired_contrasts.csv',
                        'pairing_inventory.csv', 'summary.json', 'results.md', 'table.tex',
                        'live_co_primary.png', 'live_co_primary.pdf']
            for name in expected:
                path = output / name
                self.assertTrue(path.exists(), name)
                self.assertGreater(path.stat().st_size, 0, name)
            loaded = json.loads((output / 'summary.json').read_text(encoding='utf8'))
            self.assertIn('does not run a live study', loaded['scope'])
            self.assertEqual(loaded['input']['episodes'], 10)
            self.assertEqual(loaded['analysis']['bootstrap']['seed'], 11)
            self.assertEqual(loaded['analysis']['bootstrap']['replicates'], 40)
            self.assertEqual(loaded['input']['status_counts'],
                             {'completed': 8, 'error': 1, 'invalid': 1})
            aggregate = pd.read_csv(output / 'aggregate.csv')
            self.assertEqual(len(aggregate), 4)
            for column in ('episodes_total', 'status_error', 'status_invalid',
                           'illegitimate_completed', 'legitimate_completed',
                           'illegitimate_unsafe_execution_rate',
                           'legitimate_completion_rate',
                           'illegitimate_unsafe_execution_all_status_rate'):
                self.assertIn(column, aggregate.columns)
            denominators = pd.read_csv(output / 'endpoint_denominators.csv')
            self.assertEqual(len(denominators), 8)
            self.assertEqual(set(denominators['endpoint']),
                             {'unsafe_execution', 'legitimate_completion'})
            contrasts = pd.read_csv(output / 'paired_contrasts.csv')
            self.assertEqual(len(contrasts), 12)
            inventory = pd.read_csv(output / 'pairing_inventory.csv')
            self.assertEqual(len(inventory), 2)
            with Image.open(output / 'live_co_primary.png') as image:
                dpi = image.info.get('dpi')
                self.assertIsNotNone(dpi)
                self.assertEqual(round(dpi[0]), 320)
                corner = image.convert('RGB').getpixel((2, 2))
                self.assertTrue(all(channel > 240 for channel in corner))
            self.assertTrue((output / 'live_co_primary.pdf').read_bytes().startswith(b'%PDF'))
            markdown_text = (output / 'results.md').read_text(encoding='utf8')
            self.assertIn('does not run a live study', markdown_text)
            self.assertIn('Paired family contrasts', markdown_text)
            latex_text = (output / 'table.tex').read_text(encoding='utf8')
            self.assertIn(r'\begin{longtable}', latex_text)
            self.assertIn(r'\label{tab:live-co-primary}', latex_text)

    def test_cli_rejects_missing_field_without_writing_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / 'episodes.jsonl'
            output = root / 'analysis'
            record = make_record('broken', 'claims')
            del record['family_id']
            input_path.write_text(json.dumps(record) + '\n', encoding='utf8')
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(['--input', str(input_path), '--output', str(output)])
            self.assertEqual(code, 2)
            self.assertIn('family_id', stderr.getvalue())
            self.assertFalse((output / 'summary.json').exists())
            self.assertFalse((output / 'results.md').exists())


class LiveBoundaryIntegrationTests(unittest.TestCase):
    def test_failed_calls_can_preserve_unknown_resource_usage(self):
        row = make_record('unknown-cost', 'claims', status='error')
        row.update(usage=None, cost_units=None, latency_seconds=None)
        records = validate_episodes([(1, row)])
        aggregate = aggregate_metrics(records, seed=7, replicates=20, ci=0.95)[0]
        self.assertEqual(aggregate['episodes_total'], 1)
        self.assertEqual(aggregate['status_error'], 1)
        self.assertEqual(aggregate['usage_unknown_episodes'], 1)
        self.assertEqual(aggregate['cost_unknown_episodes'], 1)

    def test_supervisor_can_corrupt_an_honest_candidate_summary(self):
        row = make_record('honest-mistake', 'claims', legitimate=True,
                          unsafe_attempt=True, unsafe_execution=True,
                          legitimate_completion=False)
        records = validate_episodes([(1, row)])
        aggregate = aggregate_metrics(records, seed=7, replicates=20, ci=0.95)[0]
        self.assertEqual(aggregate['legitimate_unsafe_executions'], 1)
        self.assertEqual(aggregate['legitimate_completions'], 0)


if __name__ == '__main__':
    unittest.main()

