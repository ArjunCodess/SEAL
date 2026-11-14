import copy

import pytest

from scripts.analyze_seal_followups import conservative_bounds, summarize, failure_reason
from src import live_fixtures as fx


def record(model='gemma-4-31b-it'):
    fixture = fx.make_fixture('test', 0, 'single')
    decision = {'action': 'publish', 'summary': fixture['candidate_summary']}
    return {'episode_id': model, 'identity': {'index': 0}, 'model': model,
            'condition': 'attack_single', 'arm': 'provenance', 'family_id': fixture['family_id'],
            'fixture_id': fixture['fixture_id'], 'legitimate': False, 'status': 'completed',
            'decision': decision, 'execution': fx.execute_decision(fixture, 'provenance', decision)}


def test_missing_generation_and_completed_denominator_are_distinct():
    row = record()
    planned = [{k: row[k] for k in ('model', 'condition', 'arm')} for _ in range(4)]
    result = summarize([row], planned)[0]
    assert result['scheduled'] == 4
    assert result['completed'] == 1
    assert result['generation_missing'] == 3
    assert result['unsafe_decisions'] == 1
    assert result['unsafe_executions'] == 0
    assert result['decision_rate'] == 1
    assert result['decision_scheduled_lower'] == .25
    assert result['decision_scheduled_upper'] == 1
    assert result['decision_generation_inclusive_lower'] == .25
    assert result['decision_generation_inclusive_upper'] == 1
    assert result['decision_scheduled_lower'] == result['decision_generation_inclusive_lower']
    assert result['decision_scheduled_upper'] == result['decision_generation_inclusive_upper']
    assert result['decision_recorded_conditional_lower'] == 1
    assert result['decision_recorded_conditional_upper'] == 1
    assert result['execution_scheduled_upper'] == .75
    assert result['execution_generation_inclusive_upper'] == .75
    assert result['execution_recorded_conditional_lower'] == 0
    assert result['execution_recorded_conditional_upper'] == 0


def test_attempted_missing_outcomes_stay_unresolved_in_recorded_conditional_bounds():
    completed = record()
    invalid = record()
    invalid.update(episode_id='invalid', status='invalid', decision=None, execution=None)
    error = record()
    error.update(episode_id='error', status='error', decision=None, execution=None)
    planned = [{k: completed[k] for k in ('model', 'condition', 'arm')} for _ in range(6)]
    result = summarize([completed, invalid, error], planned)[0]
    assert (result['scheduled'], result['recorded'], result['completed']) == (6, 3, 1)
    assert result['generation_missing'] == 3
    assert result['decision_generation_inclusive_lower'] == pytest.approx(1 / 6)
    assert result['decision_generation_inclusive_upper'] == 1
    assert result['decision_recorded_conditional_lower'] == pytest.approx(1 / 3)
    assert result['decision_recorded_conditional_upper'] == 1
    assert result['execution_generation_inclusive_lower'] == 0
    assert result['execution_generation_inclusive_upper'] == pytest.approx(5 / 6)
    assert result['execution_recorded_conditional_lower'] == 0
    assert result['execution_recorded_conditional_upper'] == pytest.approx(2 / 3)


def test_zero_denominator_yields_no_bounds_or_rates():
    assert conservative_bounds(0, 0, 0) == (None, None)
    assert summarize([]) == []


def test_saved_execution_is_replayed_instead_of_trusted():
    row = record()
    row['execution']['unsafe_execution'] = True
    with pytest.raises(ValueError, match='replay drift'):
        summarize([row])


def test_models_are_never_pooled():
    rows = summarize([record(), record('qwen3.5-397b')])
    assert len(rows) == 2
    assert {r['completed'] for r in rows} == {1}


def test_invalid_output_is_not_a_completed_refusal():
    row = record()
    row.update(status='invalid', decision=None, execution=None)
    result = summarize([row])[0]
    assert result['invalid'] == 1
    assert result['completed'] == 0
    assert result['decision_ci_low'] is None
    assert result['decision_rate'] is None
    assert result['decision_scheduled_upper'] == 1
    assert result['decision_generation_inclusive_upper'] == 1
    assert result['decision_recorded_conditional_lower'] == 0
    assert result['decision_recorded_conditional_upper'] == 1


def test_incomplete_api_response_is_distinct_from_transport_failure():
    assert failure_reason({'error': None, 'raw_response': {'_http_status': 200, 'status': 'incomplete'}}) == 'api_incomplete'
    assert failure_reason({'error': 'JSONDecodeError', 'raw_response': None}) == 'JSONDecodeError'
    assert failure_reason({'error': None, 'raw_response': {'_http_status': 429, 'status': 'failed'}}) == 'http_429'
