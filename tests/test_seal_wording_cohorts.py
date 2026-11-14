import pytest

from scripts import seal_wording_cohorts as cohorts
from src import seal_followups as follow


def test_diagnostic_never_becomes_primary_when_primary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cohorts, 'ROOT', tmp_path)
    follow.write_json(tmp_path / 'data/wording_cohort_selection.json', {
        'primary': 'data/study_wording_v3', 'diagnostic': 'data/study_wording_v2',
        'diagnostic_checksums_sha256': 'frozen-diagnostic'})
    monkeypatch.setattr(cohorts.follow, 'require_frozen', lambda path: 'frozen-diagnostic')
    result = cohorts.frozen_cohorts()
    assert list(result) == ['diagnostic']


def test_changed_diagnostic_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cohorts, 'ROOT', tmp_path)
    follow.write_json(tmp_path / 'data/wording_cohort_selection.json', {
        'primary': 'data/study_wording_v3', 'diagnostic': 'data/study_wording_v2',
        'diagnostic_checksums_sha256': 'original'})
    monkeypatch.setattr(cohorts.follow, 'require_frozen', lambda path: 'changed')
    with pytest.raises(ValueError, match='differs from preselection'):
        cohorts.frozen_cohorts()


def test_no_selection_means_no_primary(tmp_path, monkeypatch):
    monkeypatch.setattr(cohorts, 'ROOT', tmp_path)
    assert cohorts.frozen_cohorts() == {}
