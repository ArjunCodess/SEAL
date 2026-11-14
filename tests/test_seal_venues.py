"""Tests for the AGENTVERIFY venue artifact generator."""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "venues" / "agentverify"
PAPER = ROOT / "paper" / "venues" / "agentverify"


def test_frozen_inputs_verify():
    from scripts.prepare_seal_venues import verify_frozen_inputs

    result = verify_frozen_inputs()
    assert result["study_files_verified"] == 26
    assert result["frozen_sources_verified"] == 5
    assert result["source_set_verified"] is True


def test_derived_artifacts_validate():
    from scripts.prepare_seal_venues import load_derived

    derived = load_derived(ROOT / "build")
    assert len(derived["records"]) == 960
    assert derived["status_counts"] == {"completed": 909, "invalid": 29, "error": 22}
    assert derived["crosscheck"]["all_passed"] is True
    assert int(derived["gate_manifest"]["total_mutations_evaluated"]) == 400
    assert derived["gate_manifest"]["observed_gate_actions"]["block"] == 300
    assert derived["gate_manifest"]["observed_gate_actions"]["abstain"] == 100
    assert derived["gate_manifest"]["observed_gate_actions"].get("publish", 0) == 0


def test_generated_files_exist():
    expected = [
        PAPER / "agentverify.tex",
        PAPER / "generated" / "macros.tex",
        PAPER / "generated" / "table_endpoints.tex",
        PAPER / "generated" / "table_mutations.tex",
        PAPER / "generated" / "table_pairing.tex",
        PAPER / "generated" / "abstract.tex",
        BUILD / "agentverify.pdf",
        BUILD / "evidence_manifest.json",
        BUILD / "claim_ledger.json",
        BUILD / "build_status.json",
    ]
    for path in expected:
        assert path.is_file(), f"Missing {path}"
        assert path.stat().st_size > 0


def test_macros_are_derived_from_tables():
    from scripts.prepare_seal_venues import compute_macros, load_derived

    derived = load_derived(ROOT / "build")
    macros = compute_macros(derived)
    # Status counts must come from the episode ledger, not constants.
    assert macros["SealTotalEpisodes"] == "960"
    assert macros["SealCompleted"] == "909"
    assert macros["SealInvalid"] == "29"
    assert macros["SealError"] == "22"
    # Provenance endpoints must agree with the generated two-endpoint table.
    two = derived["two_endpoints"]
    prov = two[(two["arm"] == "provenance") & (two["coordination"].isin(["single", "coordinated"]))]
    assert macros["ProvenanceDecisions"] == str(int(prov["decision_numerator"].sum()))
    assert macros["ProvenanceExecutions"] == str(int(prov["execution_numerator"].sum()))
    # Mutation counts must agree with the generated manifest.
    assert macros["MutationTotal"] == str(int(derived["gate_manifest"]["total_mutations_evaluated"]))
    assert macros["MutationBlocked"] == str(int(derived["gate_manifest"]["observed_gate_actions"]["block"]))
    assert macros["MutationAbstained"] == str(int(derived["gate_manifest"]["observed_gate_actions"]["abstain"]))
    assert macros["MutationPublished"] == str(int(derived["gate_manifest"]["observed_gate_actions"].get("publish", 0)))


def test_abstract_word_count():
    from scripts.prepare_seal_venues import build_abstract, compute_macros, load_derived, validate_abstract

    derived = load_derived(ROOT / "build")
    macros = compute_macros(derived)
    text = build_abstract(macros)
    words = validate_abstract(text)
    assert 150 <= words <= 250


def test_pdf_page_count_and_anonymity():
    pdf_path = BUILD / "agentverify.pdf"
    assert pdf_path.is_file()
    with fitz.open(pdf_path) as document:
        assert document.page_count <= 2
        text = "\n".join(page.get_text() for page in document)
        metadata = document.metadata or {}
    lowered = text.lower()
    for forbidden in ("arjun", "github.com", "repository tasks", "blind provenance rate"):
        assert forbidden not in lowered, f"Found forbidden text: {forbidden}"
    assert "anonymous" in lowered
    # PDF metadata must not name an author.
    assert not metadata.get("author") or "anonymous" in str(metadata.get("author")).lower()


def test_claim_ledger_structure():
    manifest = json.loads((BUILD / "claim_ledger.json").read_text(encoding="utf8"))
    assert manifest["venue"] == "AGENTVERIFY"
    assert isinstance(manifest["claims"], list) and len(manifest["claims"]) >= 5
    for claim in manifest["claims"]:
        assert "id" in claim
        assert "claim" in claim
        assert "value" in claim
        assert "numerator" in claim
        assert "denominator" in claim
        assert "source" in claim


def test_visual_qa_images_exist():
    qa_dir = BUILD / "qa"
    assert (qa_dir / "page_1.png").is_file()
    assert (qa_dir / "page_2.png").is_file()


def test_unimplemented_venues_raise():
    from scripts.prepare_seal_venues import prepare_venue

    with pytest.raises(NotImplementedError):
        prepare_venue("ADMIT", BUILD, PAPER, False)