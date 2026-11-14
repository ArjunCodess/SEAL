import pytest

from scripts.freeze_blind_evidence import freeze
from src import seal_collection as sc


class FailedTransport:
    def create(self, **payload):
        raise ConnectionError('offline transport failure')


class SuccessfulTransport:
    def create(self, **payload):
        return {'status': 'completed', '_http_status': 200,
                'output_text': '{"action":"publish","summary":{}}',
                'usage': {'input_tokens': 10, 'output_tokens': 5}}


def test_unavailable_bodies_are_null_failures_not_model_observations(tmp_path):
    directory = tmp_path / 'failed'
    sc.collect(directory, budget_config_path=sc.ROOT / 'configs/seal/stage4_budget.json',
               fixture_indices=[0], client=FailedTransport())
    with pytest.raises(sc.SealFreezeError, match='without raw response'):
        sc.freeze_study(directory)
    result = freeze(directory)
    assert result['status_counts'] == {'failed': 6}
    events = sc.load_responses_log(directory / 'transport_failure_events.jsonl')
    assert len(events) == 6
    assert all(r['raw_response'] is None for r in events)
    assert all(r['schema'] == 'seal-blind-terminal-transport-failure-event-v1' for r in events)
    assert sc.read_json(directory / 'freeze_manifest.json')['responses_ledger']['rows'] == 0
    sc.verify_study_checksums(directory)


def test_missing_successful_response_is_not_waived(tmp_path):
    directory = tmp_path / 'missing'
    sc.collect(directory, budget_config_path=sc.ROOT / 'configs/seal/stage4_budget.json',
               fixture_indices=[0], client=SuccessfulTransport())
    (directory / 'responses.jsonl').unlink()
    with pytest.raises(ValueError, match='not a terminal transport failure'):
        freeze(directory)
    assert not (directory / 'checksums.json').exists()
