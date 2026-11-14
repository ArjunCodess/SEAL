import json

import pytest

from scripts import seal_worker_audit as audit
from src import live_fixtures as fx, live_workers as workers, seal_followups as follow


def test_copy_count_is_observed_and_prompt_hash_is_required(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    root = tmp_path / 'data/study/workers'
    fixture = fx.make_fixture('test', 0, 'single')
    baseline = fixture['worker_messages'][0]
    user = workers.build_worker_user_input(role=baseline['role'], fixture=fixture,
        coordination='single', message_index=0, prior_messages=[], scripted_baseline=baseline)
    payload = workers.build_worker_request_payload('gemma-4-31b-it', user, max_output_tokens=512)
    follow.write_json(root / 'worker_corpus.json', {fixture['fixture_id']: [baseline]})
    row = {'status': 'accepted', 'model_id': 'gemma-4-31b-it',
           'accepted_message': {'role': baseline['role'], 'content': 'Different wording'},
           'prompt_sha256': follow.digest(payload), 'raw_response': {'max_output_tokens': 512},
           'provenance': {'split': 'test', 'fixture_index': 0, 'fixture_id': fixture['fixture_id'],
                          'coordination': 'single', 'message_index': 0}}
    path = root / 'worker_generation_audit.jsonl'
    path.write_text(json.dumps(row) + '\n')
    result = audit.original_worker_audit()
    assert result['accepted'] == result['hash_verified_prompts_with_reference'] == 1
    assert result['exact_copies'] == 0
    row['prompt_sha256'] = 'incorrect'
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='differs from recorded hash'):
        audit.original_worker_audit()
