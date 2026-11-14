"""Measure original copying and verify that hashed prompts exposed the template."""
import json
from pathlib import Path

from src import live_fixtures as fx, live_workers as workers, seal_followups as follow

ROOT = Path(__file__).resolve().parents[1]


def original_worker_audit():
    root = ROOT / 'data/study/workers'
    corpus = json.loads((root / 'worker_corpus.json').read_text(encoding='utf8'))
    records = [json.loads(line) for line in (root / 'worker_generation_audit.jsonl').read_text(encoding='utf8').splitlines()]
    accepted = [row for row in records if row['status'] == 'accepted']
    copied = 0
    for record in accepted:
        provenance = record['provenance']
        fixture = fx.make_fixture(provenance['split'], provenance['fixture_index'], provenance['coordination'])
        index = provenance['message_index']
        baseline = fixture['worker_messages'][index]
        user_input = workers.build_worker_user_input(role=baseline['role'], fixture=fixture,
            coordination=provenance['coordination'], message_index=index,
            prior_messages=corpus[provenance['fixture_id']][:index], scripted_baseline=baseline)
        payload = workers.build_worker_request_payload(record['model_id'], user_input,
            max_output_tokens=record['raw_response']['max_output_tokens'])
        if follow.digest(payload) != record['prompt_sha256']:
            raise ValueError('Reconstructed original worker prompt differs from recorded hash')
        if json.loads(user_input)['wording_reference'] != baseline:
            raise ValueError('Original prompt did not expose the corresponding template')
        copied += record['accepted_message']['content'] == baseline['content']
    return {'accepted': len(accepted), 'exact_copies': copied,
            'hash_verified_prompts_with_reference': len(accepted), 'copy_definition': 'Exact content string equality'}
