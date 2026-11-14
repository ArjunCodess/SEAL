import pytest
from scripts.resume_wording_validation import validate_totals


def test_numeric_and_string_totals_are_both_validated():
    validate_totals({'employees': 3, 'gross_pay': 200, 'period': '2026-w10'},
                    'For period 2026-W10, all 3 employees total 200.\nclaim_status: complete')
    validate_totals({'conflicts': 2, 'events': 5, 'window': '14:00-15:00'},
                    'The 5 events from 14:00 to 15:00 have 2 conflicts.\nclaim_status: complete')


def test_missing_string_is_not_silently_accepted():
    with pytest.raises(ValueError, match='string total'):
        validate_totals({'employees': 3, 'period': '2026-w10'}, '3 employees in 2025-W10')


def test_decimal_spelling_does_not_force_template_wording():
    validate_totals({'count': 3, 'offset': 8}, 'count 3.0 and offset 8.00')
