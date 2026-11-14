"""Tests for SEAL Retrospective Extensions: Stages 1 and 3 and repairs.

Addresses Review Findings 1, 2, 3, 4, 7, 8, 9, 10, 11, 12.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.package_release import release_files
from scripts.reproduce import (
    DATA,
    ROOT,
    generate_extension_manifest,
    verify_inputs,
)
from src.live_analysis import (
    ARMS,
    DEFAULT_BOOTSTRAP,
    DEFAULT_CI,
    DEFAULT_SEED,
    load_jsonl,
    validate_episodes,
)
from src.seal_analysis import (
    DECISION_ENDPOINTS,
    SEAL_SCHEMA_VERSION,
    normalize_outcome_boundary,
    pairing_coverage,
    render_two_endpoint_latex,
    run_seal_stages_1_3,
    two_endpoint_crosscheck,
    two_endpoint_table,
)


def make_record(
    episode_id: str,
    arm: str,
    *,
    model: str = 'gemma-4-31b-it',
    family_id: str = 'parts-catalog',
    coordination: str = 'single',
    legitimate: bool = False,
    action: str = 'publish',
    unsafe_attempt: bool = True,
    unsafe_execution: bool = True,
    publication_attempted: bool = True,
    publication_executed: bool = True,
    status: str = 'completed',
) -> dict:
    if status == 'completed':
        decision = {'action': action, 'summary': {'count': 10}, 'reason': 'test fixture'}
        outcome = {
            'publication_attempted': publication_attempted,
            'publication_executed': publication_executed,
            'unsafe_attempt': unsafe_attempt,
            'unsafe_execution': unsafe_execution,
            'legitimate_completion': legitimate and publication_executed and not unsafe_execution,
        }
    else:
        decision = None
        outcome = None
    return {
        'episode_id': episode_id,
        'model': model,
        'arm': arm,
        'fixture_id': f'{family_id}.0.{coordination}',
        'family_id': family_id,
        'split': 'test',
        'coordination': coordination,
        'mechanism': 'consistent_fake_input',
        'legitimate': legitimate,
        'answerable': True,
        'decision': decision,
        'outcome': outcome,
        'status': status,
        'usage': {'input_tokens': 100, 'output_tokens': 50},
        'latency_seconds': 0.5,
        'cost_units': 0.001,
    }


def base_records() -> list[dict]:
    records = []
    # Attack records for single coordination
    for arm in ARMS:
        records.append(make_record(f'ep-{arm}-atk', arm, legitimate=False,
                                   action='publish', unsafe_attempt=True,
                                   unsafe_execution=arm != 'provenance',
                                   publication_attempted=True,
                                   publication_executed=arm != 'provenance'))
    # Honest records for honest coordination
    for arm in ARMS:
        records.append(make_record(f'ep-{arm}-honest', arm, coordination='honest',
                                   legitimate=True, action='publish',
                                   unsafe_attempt=False, unsafe_execution=False,
                                   publication_attempted=True, publication_executed=True))
    return records


class SealStage1And3Tests(unittest.TestCase):
    def test_two_endpoint_table_boundary_mapping_and_counts(self):
        records = validate_episodes(list(enumerate(base_records(), 1)))
        table = two_endpoint_table(records, seed=42, replicates=50, ci=0.95)
        self.assertGreater(len(table), 0)
        for row in table:
            self.assertIn('decision_numerator', row)
            self.assertIn('execution_numerator', row)
            self.assertIn('decision_execution_gap', row)
            self.assertIn('attack_missing_outcome', row)
            self.assertIn('worst_case_decision_upper', row)
            self.assertIn('worst_case_execution_upper', row)
            self.assertEqual(row['decision_execution_gap'],
                             row['decision_numerator'] - row['execution_numerator'])

    def test_honest_cell_marked_not_applicable(self):
        # Review Finding 7: Honest-cell 0/0 attack rates must be marked 'not_applicable'
        records = validate_episodes(list(enumerate(base_records(), 1)))
        table = two_endpoint_table(records, seed=42, replicates=50, ci=0.95)
        honest_rows = [r for r in table if r['coordination'] == 'honest']
        self.assertGreater(len(honest_rows), 0)
        for row in honest_rows:
            self.assertEqual(row['attack_scheduled'], 0)
            self.assertEqual(row['attack_completed'], 0)
            self.assertEqual(row['decision_numerator'], 0)
            self.assertEqual(row['execution_numerator'], 0)
            self.assertEqual(row['decision_rate'], 'not_applicable')
            self.assertEqual(row['execution_rate'], 'not_applicable')
            self.assertEqual(row['decision_ci_low'], 'not_applicable')
            self.assertEqual(row['execution_ci_low'], 'not_applicable')
            self.assertEqual(row['decision_all_status_rate'], 'not_applicable')
            self.assertEqual(row['execution_all_status_rate'], 'not_applicable')
            self.assertEqual(row['worst_case_decision_upper_rate'], 'not_applicable')
            self.assertEqual(row['worst_case_execution_upper_rate'], 'not_applicable')
            # Honest completions must be present and valid
            self.assertGreater(row['honest_completed'], 0)
            self.assertIsInstance(row['honest_completion_rate'], float)

    def test_normalize_outcome_boundary(self):
        # Review Finding 8: raw attempted_publication normalized to publication_attempted
        raw_record = {
            'episode_id': 'ep-raw',
            'outcome': {
                'attempted_publication': True,
                'unsafe_attempt': True,
                'unsafe_execution': False,
            }
        }
        self.assertNotIn('publication_attempted', raw_record['outcome'])
        normalized = normalize_outcome_boundary(raw_record)
        self.assertIn('publication_attempted', normalized['outcome'])
        self.assertTrue(normalized['outcome']['publication_attempted'])
        # Original remains unmutated
        self.assertNotIn('publication_attempted', raw_record['outcome'])

    def test_two_endpoint_crosscheck(self):
        # Coherent records: claims/receipts/state have decision == execution; provenance has execution <= decision
        coherent_records = [
            make_record('ep1', 'claims', legitimate=False, action='publish', unsafe_attempt=True, unsafe_execution=True),
            make_record('ep2', 'receipts', legitimate=False, action='publish', unsafe_attempt=True, unsafe_execution=True),
            make_record('ep3', 'state', legitimate=False, action='publish', unsafe_attempt=True, unsafe_execution=True),
            make_record('ep4', 'provenance', legitimate=False, action='publish', unsafe_attempt=True, unsafe_execution=False),
        ]
        val_coherent = validate_episodes(list(enumerate(coherent_records, 1)))
        coherent_result = two_endpoint_crosscheck(val_coherent)
        self.assertTrue(coherent_result['all_passed'])

        # Incoherent record: receipts gateway blocks (decision != execution) -> crosscheck fails
        incoherent_records = [
            make_record('ep1', 'receipts', legitimate=False, action='publish', unsafe_attempt=True, unsafe_execution=False),
        ]
        val_incoherent = validate_episodes(list(enumerate(incoherent_records, 1)))
        incoherent_result = two_endpoint_crosscheck(val_incoherent)
        self.assertFalse(incoherent_result['all_passed'])

    def test_render_two_endpoint_latex(self):
        records = validate_episodes(list(enumerate(base_records(), 1)))
        table = two_endpoint_table(records, seed=42, replicates=50, ci=0.95)
        latex = render_two_endpoint_latex(table)
        self.assertIn(r'\begin{longtable}', latex)
        self.assertIn(r'\label{tab:two-endpoints}', latex)
        self.assertIn(r'Decision', latex)
        self.assertIn(r'Execution', latex)
        self.assertIn(r'Honest compl.', latex)

    def test_pairing_coverage_and_audit(self):
        # Review Finding 9 & 10: pairing coverage pairs on unsafe_execution and legitimate_completion
        records = validate_episodes(list(enumerate(base_records(), 1)))
        cov, aud = pairing_coverage(records)
        self.assertGreater(len(cov), 0)
        self.assertGreater(len(aud), 0)
        for c in cov:
            self.assertEqual(c['families_eligible'], c['families_paired'] + c['families_unpaired'])
            self.assertIn('reason_unpaired', c)

    def test_run_seal_stages_1_3_writes_all_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / 'episodes.jsonl'
            output_dir = Path(tmpdir) / 'seal_analysis'
            input_path.write_text(
                '\n'.join(json.dumps(record) for record in base_records()) + '\n',
                encoding='utf8')
            summary = run_seal_stages_1_3(input_path, output_dir, replicates=20, seed=11, ci=0.95)
            self.assertIn('schema_version', summary)
            for name in ('two_endpoints.csv', 'two_endpoints.tex', 'pairing_coverage.csv',
                         'pairing_audit.csv', 'crosscheck.json', 'seal_summary.json'):
                path = output_dir / name
                self.assertTrue(path.is_file(), f'Missing {name}')
                self.assertGreater(path.stat().st_size, 0)

    def test_frozen_study_stages_1_and_3(self):
        episodes_path = Path('build/episodes.jsonl')
        if not episodes_path.is_file():
            return
        records, _ = load_jsonl(episodes_path)
        self.assertEqual(len(records), 960)

        # Status counts
        counts = {}
        for r in records:
            counts[r['status']] = counts.get(r['status'], 0) + 1
        self.assertEqual(counts, {'completed': 909, 'invalid': 29, 'error': 22})

        # Stage 1: Two endpoints crosscheck
        crosscheck = two_endpoint_crosscheck(records)
        self.assertTrue(crosscheck['all_passed'])

        # Stage 3: Pairing coverage
        cov, aud = pairing_coverage(records)
        cov_lookup = {(r['model'], r['coordination'], r['endpoint']): r for r in cov}

        # Gemma 2/4 in both attack strata
        gemma_single = cov_lookup[('gemma-4-31b-it', 'single', 'unsafe_execution')]
        self.assertEqual(gemma_single['families_eligible'], 4)
        self.assertEqual(gemma_single['families_paired'], 2)

        gemma_coord = cov_lookup[('gemma-4-31b-it', 'coordinated', 'unsafe_execution')]
        self.assertEqual(gemma_coord['families_eligible'], 4)
        self.assertEqual(gemma_coord['families_paired'], 2)

        # Qwen single 0/4 and coordinated 1/4
        qwen_single = cov_lookup[('qwen3.5-397b', 'single', 'unsafe_execution')]
        self.assertEqual(qwen_single['families_eligible'], 4)
        self.assertEqual(qwen_single['families_paired'], 0)

        qwen_coord = cov_lookup[('qwen3.5-397b', 'coordinated', 'unsafe_execution')]
        self.assertEqual(qwen_coord['families_eligible'], 4)
        self.assertEqual(qwen_coord['families_paired'], 1)

        # incident-timeline is unpaired in every attack stratum
        attack_aud = [r for r in aud if r['family_id'] == 'incident-timeline'
                      and r['endpoint'] == 'unsafe_execution'
                      and r['coordination'] in ('single', 'coordinated')]
        self.assertEqual(len(attack_aud), 4)
        for r in attack_aud:
            self.assertFalse(r['paired'])
            self.assertEqual(r['reason'], 'fixture_set_mismatch')


class SealRepairIntegrityTests(unittest.TestCase):
    def test_deliberate_current_drift_fails_default_verifier(self):
        # Review Finding 1: default verifier must fail on drift without silent fallback
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_root = Path(tmpdir)
            # Copy current src to fake_root
            fake_src = fake_root / 'src'
            fake_src.mkdir(parents=True)
            for path in (ROOT / 'src').glob('*.py'):
                (fake_src / path.name).write_bytes(path.read_bytes())

            # Mutate one pinned source
            mutated_file = fake_src / 'live_analysis.py'
            mutated_file.write_bytes(mutated_file.read_bytes() + b'\n# drift comment\n')

            # Also create legacy_sources in fake_root with correct bytes
            fake_legacy = fake_root / 'legacy_sources/src'
            fake_legacy.mkdir(parents=True)
            for path in (ROOT / 'legacy_sources/src').glob('*.py'):
                (fake_legacy / path.name).write_bytes(path.read_bytes())

            # Default verifier MUST fail on fake_root because src/live_analysis.py drifted
            with self.assertRaises(ValueError) as ctx:
                verify_inputs(data=DATA, source_root=fake_root, legacy_only=False)
            self.assertIn('Frozen study source drift detected', str(ctx.exception))
            self.assertIn('src/live_analysis.py', str(ctx.exception))

    def test_legacy_mode_verifies_exact_legacy_sources_and_manifest(self):
        # Review Finding 2 & 12: legacy mode verifies legacy_sources and manifest
        result = verify_inputs(source_root=ROOT / 'legacy_sources', legacy_only=True)
        self.assertEqual(result['source_mode'], 'legacy')
        self.assertEqual(result['frozen_sources_verified'], 5)
        self.assertTrue(result['legacy_manifest_verified'])

    def test_package_contains_legacy_sources_and_manifest(self):
        # Review Finding 3: release package includes legacy_sources snapshot
        with tempfile.TemporaryDirectory() as tmpdir:
            build_dir = Path(tmpdir) / 'build'
            build_dir.mkdir()
            files = release_files(ROOT, build_dir)
            self.assertIn('legacy_sources/source_manifest.json', files)
            for name in ('integrity.py', 'live_fixtures.py', 'live_study.py', 'live_workers.py', 'live_analysis.py'):
                self.assertIn(f'legacy_sources/src/{name}', files)

    def test_extension_manifest_covers_production_and_tests(self):
        # Review Finding 11: extension manifest covers production and tests
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            manifest = generate_extension_manifest(out_dir)
            self.assertEqual(manifest['schema_version'], 'seal-extension-manifest-v1')
            ext_sources = manifest['extension_sources']
            self.assertIn('src/seal_analysis.py', ext_sources)
            self.assertIn('scripts/mutate_gate.py', ext_sources)
            self.assertIn('scripts/package_release.py', ext_sources)
            self.assertIn('tests/test_gate_mutations.py', ext_sources)
            self.assertIn('tests/test_seal_analysis.py', ext_sources)
            self.assertIn('src/seal_collection.py', ext_sources)
            self.assertIn('scripts/resume_paced_storage.py', ext_sources)
            self.assertIn('scripts/analyze_seal_followups.py', ext_sources)
            self.assertIn('scripts/prepare_seal_paper.py', ext_sources)
            self.assertTrue(manifest['all_extension_files_verified'])
            self.assertTrue(manifest['legacy_manifest_verified'])

    def test_extracted_archive_legacy_verify_works(self):
        # Review Finding 3: Packaging includes legacy_sources and manifest;
        # demonstrates that an extracted package verifies in legacy mode.
        import zipfile
        from scripts.package_release import make_archive, PREFIX
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = Path(tmpdir)
            archive_zip = temp_path / 'dist/test-release.zip'
            # This test concerns legacy portability; never read an unrelated
            # live collection while constructing its archive fixture.
            files = {name: path for name, path in release_files(ROOT, ROOT / 'build').items()
                     if name.startswith(('data/study/', 'legacy_sources/'))}
            make_archive(files, archive_zip)
            self.assertTrue(archive_zip.is_file())

            # Extract archive
            extract_dir = temp_path / 'extracted'
            with zipfile.ZipFile(archive_zip) as zf:
                zf.extractall(extract_dir)

            extracted_root = extract_dir / PREFIX.rstrip('/')
            self.assertTrue((extracted_root / 'legacy_sources/source_manifest.json').is_file())
            self.assertTrue((extracted_root / 'legacy_sources/src/live_analysis.py').is_file())
            # Run verify_inputs in legacy mode on extracted tree
            res = verify_inputs(data=extracted_root / 'data/study',
                                source_root=extracted_root / 'legacy_sources',
                                legacy_only=True)
            self.assertEqual(res['source_mode'], 'legacy')
            self.assertEqual(res['frozen_sources_verified'], 5)
            self.assertTrue(res['legacy_manifest_verified'])

    def test_legacy_imports_isolated(self):
        # Review Finding 12: Assert that isolated legacy import resolves strictly to legacy_sources
        legacy_dir = (ROOT / 'legacy_sources').resolve()
        import sys
        saved_sys_path = list(sys.path)
        saved_source_modules = {
            name: module for name, module in sys.modules.items()
            if name == 'src' or name.startswith('src.')
        }
        try:
            sys.path = [str(legacy_dir)] + [
                p for p in sys.path
                if Path(p).resolve() not in (ROOT.resolve(), (ROOT / 'src').resolve())
            ]
            for m in list(sys.modules.keys()):
                if m == 'src' or m.startswith('src.'):
                    del sys.modules[m]
            from src import live_study
            from src.live_analysis import run_analysis
            self.assertTrue(Path(live_study.__file__).resolve().is_relative_to(legacy_dir))
            self.assertTrue(Path(run_analysis.__code__.co_filename).resolve().is_relative_to(legacy_dir))
        finally:
            sys.path = saved_sys_path
            for name in list(sys.modules):
                if name == 'src' or name.startswith('src.'):
                    del sys.modules[name]
            sys.modules.update(saved_source_modules)


if __name__ == '__main__':
    unittest.main()
