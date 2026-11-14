"""Freeze amendment for terminal transport failures with no returned JSON body.

The collection implementation and original response log remain byte-identical.
Failure events contain a hash-verified reconstructed request and a NULL response,
never a fabricated model response or decision. No inference is performed.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import live_study as ls, seal_collection as sc, seal_followups as follow


def freeze(directory):
    directory = Path(directory).resolve()
    sc.verify_source_contract()
    sc.verify_original_pins()
    if (directory / 'checksums.json').exists():
        sc.verify_study_checksums(directory)
        return sc.read_json(directory / 'freeze_manifest.json')
    protocol = sc.read_json(directory / 'protocol.json')
    if protocol['blinding']['implementation_sha256'] != sc.sha256_file(ROOT / 'src/seal_collection.py'):
        raise ValueError('Collection implementation drift; amendment cannot substitute for original source')
    records = sc.load_episode_records(directory)
    real_rows = sc.load_responses_log(directory / 'responses.jsonl')
    returned = {r['request_sha256'] for r in real_rows}
    corpus = sc.read_json(sc.WORKER_CORPUS_PATH)
    failures, failure_ids = [], []
    for eid, record in sorted(records.items()):
        if record['request_sha256'] in returned:
            continue
        if record['status'] != 'failed' or record.get('supervisor_decision') is not None:
            raise ValueError(f'Missing response is not a terminal transport failure: {eid}')
        fixture = sc.live_fixtures.make_fixture('test', record['seed_ids']['fixture_index'], record['coordination'])
        fixture = ls.apply_worker_corpus(fixture, corpus, allow_scripted_workers=False)
        with sc.install_blinding():
            observation = sc.live_fixtures.supervisor_observation(fixture, 'provenance')
        payload = ls.build_request_payload(record['model'], observation,
                  max_output_tokens=protocol['generation_settings']['max_output_tokens'])
        text = ls.canonical_json(payload)
        if follow.digest(payload) != record['request_sha256']:
            raise ValueError(f'Reconstructed request hash differs: {eid}')
        failures.append({'schema': 'seal-blind-terminal-transport-failure-event-v1',
            'recorded_at_unix': time.time(), 'model': record['model'],
            'request_sha256': record['request_sha256'], 'request_utf8_bytes': len(text.encode('utf8')),
            'request_input': text, 'raw_response': None, 'raw_response_sha256': follow.digest(None),
            'blinding_marker_verified': True})
        failure_ids.append(eid)
    if len(failure_ids) + len(real_rows) != len(records):
        raise ValueError('Failure events and returned responses do not reconcile one-to-one')
    amendment = {'schema': 'seal-freeze-transport-absence-amendment-v1',
        'collection_protocol_unchanged': True, 'inference_calls_added': 0,
        'original_returned_response_log_sha256': sc.sha256_file(directory / 'responses.jsonl'),
        'returned_response_records': len(real_rows), 'unavailable_response_records': len(failures),
        'unavailable_response_episode_ids': failure_ids,
        'failure_event_semantics': 'Request reconstructed from frozen inputs and hash-checked; response is unavailable (null), not synthetic model output',
        'freezer_sha256': sc.sha256_file(Path(__file__))}
    follow.write_json(directory / 'freeze_amendment.json', amendment)
    events_path = directory / 'transport_failure_events.jsonl'
    events_path.write_text(''.join(ls.canonical_json(r) + '\n' for r in failures), encoding='utf8')
    source_copy = directory / 'sources/scripts/freeze_blind_evidence.py'
    source_copy.parent.mkdir(parents=True, exist_ok=True)
    source_copy.write_bytes(Path(__file__).read_bytes())
    original_loader = sc.load_responses_log
    # Adapt only the freeze-time coverage check. Decisions, response bodies,
    # ledgers, gate replay, schedule, and source integrity checks are unchanged.
    def coverage_records(path):
        rows = original_loader(path)
        return rows + failures if Path(path).resolve() == (directory / 'responses.jsonl').resolve() else rows
    sc.load_responses_log = coverage_records
    try:
        result = sc.freeze_study(directory)
    finally:
        sc.load_responses_log = original_loader
    manifest = sc.read_json(directory / 'freeze_manifest.json')
    manifest['responses_ledger']['rows'] = len(real_rows)
    manifest['transport_failure_events'] = {'rows': len(failures), 'path': 'transport_failure_events.jsonl',
        'amendment_sha256': sc.sha256_file(directory / 'freeze_amendment.json')}
    follow.write_json(directory / 'freeze_manifest.json', manifest)
    sc.verify_study_checksums(directory)
    return result


if __name__ == '__main__':
    print(json.dumps(freeze(sc.DEFAULT_STUDY_DIR), indent=2))
