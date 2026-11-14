# SEAL venue build status: AGENTVERIFY

Generated: 13 November 2026 (local build). Venue: AGENTVERIFY 2027 (anonymous double-blind IEEE/ICSE extended abstract).

## Artifacts

- Source: `paper/venues/agentverify/agentverify.tex`
- Generated includes: `paper/venues/agentverify/generated/`
- PDF: `build/venues/agentverify/agentverify.pdf` (also copied to `paper/venues/agentverify/agentverify.pdf`)
- Evidence manifest: `build/venues/agentverify/evidence_manifest.json`
- Claim ledger: `build/venues/agentverify/claim_ledger.json`
- Build status: `build/venues/agentverify/build_status.json`
- Visual QA images: `build/venues/agentverify/qa/page_1.png`, `page_2.png`
- Generator: `scripts/prepare_seal_venues.py`
- Tests: `tests/test_seal_venues.py`

## Verification

- Frozen inputs: `python scripts/reproduce.py --verify-only` passed (26 study files, 5 pinned sources).
- Derived SEAL artifacts regenerated/validated: 960 episodes, status counts 909 completed / 29 invalid / 22 error.
- Two-endpoint crosscheck: passed.
- Gate mutation audit: 400 rows; observed actions 300 block, 100 abstain, 0 publish; 40 positive controls published; 4 equality-mismatch controls blocked.
- Pairing: Gemma 2/4 in both attack strata; Qwen 0/4 single; Qwen 1/4 coordinated; incident-timeline unpaired in all four attack strata.
- PDF page count: 2 (limit 2, including references).
- Abstract word count: 180.
- Anonymity scan: no author name, affiliation, repository URL, or identifying citation found in extracted PDF text; PDF author metadata is `Anonymous`.
- Tests: `python -m pytest -q` passed (333 passed, 7 subtests). `tests/test_seal_venues.py` passed (9 tests).

## Warnings and limitations

- Tectonic emitted font configuration warnings (`Fontconfig error: Cannot load default config file`) but compilation succeeded.
- IEEEtran reported undefined references on the first pass only; the completed PDF has resolved table references in the final build.
- The model's image viewer is unavailable in this environment, so visual legibility was checked through extracted text and generated page images rather than direct image inspection.
- Blind provenance, gate retry, and new-wording study numbers are intentionally excluded; those stages are not frozen.
- No submission was performed; this artifact is local only.
PY