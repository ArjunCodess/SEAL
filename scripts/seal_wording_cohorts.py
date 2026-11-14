"""Resolve the preselected primary wording cohort without outcome-based fallback."""
from pathlib import Path

from src import seal_collection as sc, seal_followups as follow

ROOT = Path(__file__).resolve().parents[1]


def frozen_cohorts():
    selection_path = ROOT / 'data/wording_cohort_selection.json'
    if not selection_path.exists():
        return {}
    selection = sc.read_json(selection_path)
    if (selection['primary'], selection['diagnostic']) != (
            'data/study_wording_v3', 'data/study_wording_v2'):
        raise ValueError('Unexpected wording cohort assignment')
    diagnostic = ROOT / selection['diagnostic']
    if follow.require_frozen(diagnostic) != selection['diagnostic_checksums_sha256']:
        raise ValueError('Diagnostic evidence differs from preselection')
    cohorts = {'diagnostic': diagnostic}
    primary = ROOT / selection['primary']
    if (primary / 'checksums.json').exists():
        follow.require_frozen(primary)
        if sc.sha256_file(primary / 'operational_driver.py') != selection['driver_sha256']:
            raise ValueError('Primary operational driver differs from preselection')
        cohorts['primary'] = primary
    return cohorts
