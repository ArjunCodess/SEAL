"""Adversarial checks for the frozen development pilot, without API calls."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import run_recovery_pilot as pilot
from src.recovery_fixtures import build_cases


def test_parallel_reservations_cannot_spend_more_than_cap(tmp_path):
    book = pilot.LedgerBook(tmp_path / "ledger.json", pilot.load_ledger(tmp_path / "ledger.json"))
    with ThreadPoolExecutor(max_workers=16) as pool:
        reasons = list(pool.map(lambda i: book.try_reserve(str(i), 1.0), range(64)))
    state = book.snapshot()
    assert reasons.count(None) == 20
    assert state["reserved_total"] == pilot.SPEND_CAP
    assert len(state["episodes"]) == 20


def test_pending_requests_count_against_request_limit(tmp_path):
    ledger = pilot.load_ledger(tmp_path / "ledger.json")
    ledger["request_count"] = pilot.MAX_REQUESTS - 2
    book = pilot.LedgerBook(tmp_path / "ledger.json", ledger)
    with ThreadPoolExecutor(max_workers=8) as pool:
        reasons = list(pool.map(lambda i: book.try_reserve(str(i), 0.001), range(16)))
    assert reasons.count(None) == 2
    assert reasons.count("max_requests") == 14


def test_changed_record_cannot_use_original_archive_signature():
    case = build_cases()[0]
    archive = pilot.build_archive_index([case])
    key = Ed25519PrivateKey.generate()
    signatures = pilot.sign_archive_records(archive, key)
    changed = replace(case.records[0], body=case.records[0].body + " ")
    with pytest.raises(ValueError, match="body mismatch"):
        pilot.verify_selected_records(case.case_id, [changed], archive, key.public_key(), signatures)


def test_replaced_trust_key_cannot_validate_original_record():
    case = build_cases()[0]
    archive = pilot.build_archive_index([case])
    key = Ed25519PrivateKey.generate()
    signatures = pilot.sign_archive_records(archive, key)
    with pytest.raises(ValueError, match="signature verification"):
        pilot.verify_selected_records(
            case.case_id, case.records, archive, Ed25519PrivateKey.generate().public_key(), signatures
        )


def test_missing_archive_is_unresolved_even_with_signed_body():
    case = build_cases()[0]
    key = Ed25519PrivateKey.generate()
    signatures = pilot.sign_archive_records(pilot.build_archive_index([case]), key)
    with pytest.raises(ValueError, match="missing archive bucket"):
        pilot.verify_selected_records(case.case_id, case.records, {}, key.public_key(), signatures)
