import json

import pytest

from scripts.audit_seal_release import (
    RELEASE_STAGE_SPECS,
    audit_calls,
    build_budget_status,
    verified_release_ledger,
)
from src import live_study as ls, seal_collection as sc, seal_followups as follow


def frozen_call(tmp_path, error=False):
    class Client:
        def create(self, **payload):
            if error:
                raise TimeoutError('No response')
            return {'status': 'completed', '_http_status': 200, 'output_text': '{}',
                    'usage': {'input_tokens': 7, 'output_tokens': 3}}
    budget = ls.validate_budget_config(sc.read_json(sc.ROOT / 'configs/seal/stage4_budget.json'))
    store = follow.CallStore(tmp_path / 'study', {'name': 'release-audit-test'}, budget, client=Client())
    result = store.call({'id': 1}, ls.build_request_payload('gemma-4-31b-it', {}, max_output_tokens=100))
    store.save_result(result)
    store.freeze(1)
    return store.directory, result


def test_usage_charge_and_error_reservation_both_reconcile(tmp_path):
    successful, result = frozen_call(tmp_path / 'ok')
    assert audit_calls(successful)['charged_credits'] == result['charged_cost']
    failed, result = frozen_call(tmp_path / 'error', error=True)
    assert audit_calls(failed)['charged_credits'] == result['reservation']


def test_response_tampering_detected_even_with_recomputed_inventory(tmp_path):
    directory, result = frozen_call(tmp_path)
    path = directory / 'calls' / (result['episode_id'] + '.json')
    changed = json.loads(path.read_text())
    changed['raw_response']['output_text'] = 'altered'
    follow.write_json(path, changed)
    checksums = sc.read_json(directory / 'checksums.json')
    checksums[path.relative_to(directory).as_posix()] = sc.sha256_file(path)
    follow.write_json(directory / 'checksums.json', checksums)
    with pytest.raises(ValueError, match='Response drift'):
        audit_calls(directory)


def test_release_budget_status_counts_each_frozen_root_ledger_exactly_once():
    status = build_budget_status()
    assert status['schema'] == sc.BUDGET_STATUS_SCHEMA
    assert list(status['stages']) == [
        'stage4_blind_provenance', 'stage5_gate_retry',
        'stage6_wording_v2_diagnostic', 'stage6_wording_v3_primary']
    assert [row['ledger']['request_count'] for row in status['stages'].values()] == [240, 114, 504, 952]
    assert [row['ledger']['spent_total'] for row in status['stages'].values()] == pytest.approx(
        [1.29058114, 0.54697127, 1.43374151, 3.12142530])
    assert status['total_requests'] == 1810
    assert status['stage_order'] == list(status['stages'])
    assert status['committed_all_stages_credits'] == pytest.approx(6.39271922)
    assert status['extension_charges_total_credits'] == pytest.approx(6.39271922)
    assert status['release_total_check'] == {
        'expected_credits': 6.39271922,
        'observed_credits': pytest.approx(6.39271922),
        'matches': True,
    }
    assert status['pending_requests'] == 0
    assert status['pending_reservations_credits'] == 0
    assert status['no_pending'] is True
    assert all(row['pending_requests'] == 0 and abs(row['ledger']['reserved_total']) < 1e-8
               for row in status['stages'].values())


def test_release_budget_status_keeps_wording_ceiling_shared_and_development_separate():
    status = build_budget_status()
    envelopes = status['stage_envelopes_credits']
    assert envelopes['stage6_wording_shared'] == 100
    assert 150 not in envelopes.values()
    assert envelopes['development_and_collection_failures'] == 50
    wording = status['envelope_groups']['stage6_wording_shared']
    assert wording['stages'] == ['stage6_wording_v2_diagnostic', 'stage6_wording_v3_primary']
    assert wording['committed_credits'] == pytest.approx(4.55516681)
    assert wording['envelope_remaining_credits'] == pytest.approx(95.44483319)
    development = status['envelope_groups']['development_and_collection_failures']
    assert development['envelope_credits'] == 50
    assert development['committed_credits'] == 0
    assert development['stages'] == []


def test_release_budget_status_keeps_historical_spend_unsubtracted_and_balance_unverified():
    status = build_budget_status()
    assert status['authorized_total_credits'] == 1490
    assert status['remaining_ceiling_for_new_stages_credits'] == 1490
    assert status['authorized_balance_api_verified'] is False
    assert 'not API-verified' in status['authorized_ceiling_basis']
    assert status['historical_original_ledger_credits'] == pytest.approx(4.10737974)
    assert status['historical_original_spend_subtracted_again'] is False


def test_release_budget_status_writes_only_when_given_an_explicit_path(tmp_path):
    target = tmp_path / 'budget_status.json'
    status = build_budget_status(write_path=target)
    written = json.loads(target.read_text())
    assert written['committed_all_stages_credits'] == status['committed_all_stages_credits']
    assert written['no_pending'] is True


def test_release_budget_status_refuses_a_non_data_root(tmp_path):
    spec = dict(RELEASE_STAGE_SPECS['stage4_blind_provenance'])
    with pytest.raises(ValueError, match='non-data root'):
        verified_release_ledger(spec, directory=tmp_path / 'pytest-blind-study')


def test_pytest_build_outputs_cannot_replace_the_real_release_status():
    assert sc.BUDGET_STATUS_PATH != sc.ROOT / 'build' / 'seal' / 'budget_status.json'
    assert not sc.BUDGET_STATUS_PATH.resolve().is_relative_to((sc.ROOT / 'build').resolve())
