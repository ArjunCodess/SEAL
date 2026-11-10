from pathlib import Path
import hashlib
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from scripts.reproduce import DATA, ROOT, digest_recorded_text, read_responses, reproduce, verify_inputs
from scripts.package_release import make_archive, PREFIX


class ReproductionEvidenceTests(unittest.TestCase):
    def test_build_output_cannot_be_an_ancestor_of_frozen_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data/study'
            data.mkdir(parents=True)
            evidence = data / 'record.json'
            evidence.write_text('frozen evidence')
            with patch('scripts.reproduce.ROOT', root), patch('scripts.reproduce.DATA', data):
                with patch('scripts.reproduce.verify_inputs') as verify:
                    with self.assertRaisesRegex(ValueError, 'immutable study inputs'):
                        reproduce(root / 'data', analysis_only=True)
                    verify.assert_not_called()
            self.assertEqual(evidence.read_text(), 'frozen evidence')
            self.assertEqual(list(data.iterdir()), [evidence])

    def test_frozen_inputs_and_sources_match_recorded_hashes(self):
        result = verify_inputs()
        self.assertEqual(result['frozen_sources_verified'], 5)
        self.assertGreater(result['study_files_verified'], 20)

    def test_episode_export_hash_accepts_either_newline_style(self):
        text = '{"episode_id":"a"}\n{"episode_id":"b"}\n'
        recorded = text.replace('\n', '\r\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            lf = folder / 'lf.jsonl'
            crlf = folder / 'crlf.jsonl'
            lf.write_bytes(text.encode())
            crlf.write_bytes(recorded)
            self.assertEqual(digest_recorded_text(lf), digest_recorded_text(crlf))
            self.assertEqual(digest_recorded_text(lf), hashlib.sha256(recorded).hexdigest())

    def test_lossless_record_container_preserves_all_calls_and_failures(self):
        records = read_responses(DATA / 'main/responses.jsonl')
        self.assertEqual(len(records), 960)
        counts = {}
        for response in records:
            counts[response.record['status']] = counts.get(response.record['status'], 0) + 1
        self.assertEqual(counts, {'completed': 909, 'invalid': 29, 'failed': 22})

    def test_path_traversal_is_rejected_before_materializing_a_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'responses.jsonl'
            text = '{}'
            path.write_text(json.dumps({'file': '../other.json', 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text}) + '\n')
            with self.assertRaisesRegex(ValueError, 'filename'):
                read_responses(path)

    def test_modified_response_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'responses.jsonl'
            path.write_text(json.dumps({'file': 'a' * 64 + '.json', 'sha256': '0' * 64, 'text': '{}'}) + '\n')
            with self.assertRaisesRegex(ValueError, 'Changed recorded response'):
                read_responses(path)

    def test_changed_frozen_input_fails_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'record.json').write_text('changed')
            (root / 'checksums.json').write_text(json.dumps({'record.json': hashlib.sha256(b'original').hexdigest()}))
            with self.assertRaisesRegex(ValueError, 'Changed study input'):
                verify_inputs(root, ROOT)

    def test_release_contains_verified_bytes_and_reproduction_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'README.md';source.write_text('Reproduce from saved calls.')
            output = root / 'research.zip'
            report = make_archive({'README.md': source}, output)
            self.assertTrue(report['all_hashes_verified'])
            with zipfile.ZipFile(output) as archive:
                sums = json.loads(archive.read(PREFIX + 'checksums.json'))
                self.assertEqual(hashlib.sha256(archive.read(PREFIX + 'README.md')).hexdigest(), sums['README.md'])

    def test_release_outputs_cannot_overwrite_input_artifacts(self):
        for name in ('research.zip', 'research.zip.partial', 'research.verification.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / name
                source.write_bytes(b'frozen release input')
                with self.assertRaisesRegex(ValueError, 'overwrite an input artifact'):
                    make_archive({name: source}, root / 'research.zip')
                self.assertEqual(source.read_bytes(), b'frozen release input')
                self.assertEqual(list(root.iterdir()), [source])

if __name__ == '__main__':
    unittest.main()
