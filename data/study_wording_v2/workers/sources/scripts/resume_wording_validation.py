"""Typed-total validation amendment; resume saved calls without regeneration.

The original collector and prompts remain byte-identical. This uniformly applies
the intended factual constraint to numeric and string-valued fixture totals.
"""
import json
import re
from decimal import Decimal
from pathlib import Path

from scripts import run_wording_v2 as original
from src import seal_collection as sc, seal_followups as follow
from src import live_workers as workers


def validate_totals(totals, content):
    observed = {Decimal(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)', content)}
    normalized = re.sub(r'(?<=\d)\s*to\s*(?=\d)', '-', content.casefold())
    normalized = re.sub(r'\s+', '', normalized.replace('–', '-').replace('—', '-'))
    for value in totals.values():
        if isinstance(value, (int, float)):
            if Decimal(str(value)) not in observed:
                raise ValueError('Supplied numeric total missing from generated handoff')
        elif str(value).casefold().replace(' ', '') not in normalized:
            raise ValueError('Supplied string total missing from generated handoff')


def generate_condition(store, index, condition):
    fixture = original.fixture_for(index, condition)
    prior, records, valid = [], [], True
    for message_index, baseline in enumerate(fixture['worker_messages']):
        # This is the original payload builder, including prior accepted text.
        payload = original.worker_payload(fixture, baseline, prior, message_index)
        record = dict(store.call({'index': index, 'condition': condition, 'message': message_index}, payload))
        record.update(index=index, condition=condition, accepted_message=None, exact_copy=False,
                      validation_amendment_sha256=sc.sha256_file(Path(__file__)))
        if record['status'] == 'completed':
            try:
                message = workers.parse_worker_message_text(record['output_text'], expected_role=baseline['role'])
                workers.validate_accepted_worker_message(message, fixture=fixture)
                totals = fixture['true_summary'] if condition.startswith('honest') else fixture['candidate_summary']
                validate_totals(totals, message['content'])
                record['accepted_message'] = message
                record['exact_copy'] = message['content'] == baseline['content']
                prior.append(message)
            except ValueError as exc:
                record.update(status='invalid', parse_error=str(exc))
                valid = False
        else:
            valid = False
        records.append(record)
        store.save_result(record)
    return {'index': index, 'condition': condition, 'valid': valid,
            'messages': prior if valid else [], 'attempt_ids': [r['episode_id'] for r in records]}


def generate(args):
    directory = Path(args.study_dir) / 'workers'
    directory.mkdir(parents=True, exist_ok=True)
    amendment_path = directory / 'validation_amendment.json'
    if not amendment_path.exists():
        prior_ledger = sc.read_json(directory / 'ledger.json') if (directory / 'ledger.json').exists() else None
        amendment = {'schema': 'seal-worker-string-total-validation-amendment-v1',
            'reason': 'Original decimal-only validator cannot parse existing period/window string fields',
            'scope': 'Uniform typed factual validation of all saved and subsequent outputs',
            'prompt_changes': False, 'regeneration_of_saved_calls': False,
            'generation_attempts_per_message': 1,
            'retained_prior_calls': prior_ledger['request_count'] if prior_ledger else 0,
            'validator_sha256': sc.sha256_file(Path(__file__)),
            'prior_ledger_sha256': sc.sha256_file(directory / 'ledger.json')}
        follow.write_json(amendment_path, amendment)
        source = directory / 'sources/scripts/resume_wording_validation.py'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(Path(__file__).read_bytes())
        checkpoint = directory / 'validation_checkpoint'
        for result in (directory / 'results').glob('*.json'):
            checkpoint.mkdir(parents=True, exist_ok=True)
            (checkpoint / result.name).write_bytes(result.read_bytes())
    collector = original.generate_condition
    original.generate_condition = generate_condition
    try:
        return original.generate(args)
    finally:
        original.generate_condition = collector
