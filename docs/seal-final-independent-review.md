# SEAL final independent review

Scope: read-only offline scientific/reproducibility review. No inference calls, credentials, collection, frozen-data edits, commits, pushes, or submissions. All reported values were checked against frozen ledgers, generated CSV/JSON artifacts, and source code.

## Verified without defect

- Original record: 960 scheduled calls; 909 completed, 29 invalid, 22 errors; no missing call repaired.
- Endpoint definitions: decision = `unsafe_attempt`, execution = `unsafe_execution`; claims/receipts/state decision counts equal execution counts; provenance requests can be vetoed and execution counts are zero.
- Exact-copy audit: 280/280 accepted original worker calls copied `wording_reference`; wording cohorts have 0 exact copies.
- Paired counts: Gemma 2/4 in both attack strata; Qwen 0/4 single and 1/4 coordinated; `incident-timeline` unpaired in all four attack strata.
- Provenance zeros are code-gate enforcement, including `trusted_summary != summary` and earlier binding/authorization vetoes; true summary was shown on original provenance observations.
- Extension ledgers reconcile: blind 240 (228 completed, 6 failed, 6 invalid); retry 114 (113 completed, 1 error); wording diagnostic 320 workers (55 completed, 33 invalid, 232 HTTP 429) plus 184 supervisors (139 completed, 3 invalid, 42 errors, 8 unknown deliveries); wording primary 320 workers (201 completed, 106 invalid, 13 API-incomplete) plus 632 supervisors (565 completed, 23 invalid, 44 JSON decode errors). Extension charge total remains 6.39271922 credits.
- Storage amendment: one reservation failed before call-file creation and HTTP dispatch, was released, and no uncertain request was replayed.
- `audit_seal_release.py` and the targeted seal tests pass.

## Ranked defects and caveats

1. **Stale generated budget status** — `build/seal/budget_status.json` currently contains a pytest temporary blind-study path (`...pytest-of-USER...test_retry_eligibility_and_ori0/blind`) and omits the primary wording stage. Regenerate it with `python scripts/reproduce_all.py` / `python scripts/audit_seal_release.py` before packaging or submission. Supporting path: `build/seal/budget_status.json`.

2. **Wording all-scheduled upper bounds conflate unavailable generation with missing outcomes** — In `scripts/analyze_seal_followups.py`, `summarize()` sets `scheduled` from the planned 1,280-cell grid and computes `decision_scheduled_upper` / `execution_scheduled_upper` as `(count + scheduled - completed) / scheduled`. Unavailable worker conditions are therefore treated as potentially unsafe outcomes, even though no supervisor call was made. Either relabel these as generation-inclusive conservative bounds or compute separate transport-only bounds. Supporting path: `scripts/analyze_seal_followups.py` (`summarize`, upper-bound lines).

3. **Paired contrast inventory is execution-focused, not decision-focused** — `src/seal_analysis.py` `pairing_coverage()` pairs on `ENDPOINTS` (`unsafe_execution`, `legitimate_completion`), not `unsafe_attempt`. This is internally consistent and documented, but manuscripts must not imply that unsafe-decision contrasts have the same pairing coverage. Supporting path: `src/seal_analysis.py` (`pairing_coverage`, `ENDPOINTS`).

4. **Honest controls are pooled across coordination within model/arm** — `two_endpoint_table()` builds `honest_by_model_arm` from all `coordination == 'honest'` records, not from the attack cell's coordination. This is intentional because honest controls are three-speaker only, but it must remain explicitly labeled to avoid apparent matched-cell overclaiming. Supporting path: `src/seal_analysis.py` (`two_endpoint_table`).

5. **Cohort resolution hardcodes paths and driver filename** — `frozen_cohorts()` requires `data/study_wording_v3` / `data/study_wording_v2` and `operational_driver.py` / `storage_resume_driver.py`. This reduces portability of the review/audit workflow if directory names or driver filenames change. Supporting path: `scripts/seal_wording_cohorts.py`.

6. **No independent semantic validation of generated wording** — Acceptance is schema, role, and supplied-token validation only. Exact-copy counts are zero in both wording cohorts, but that does not establish linguistic diversity or semantic adequacy. Keep this as a stated limitation in any claim. Supporting paths: `scripts/analyze_seal_followups.py` (`validation_scope`), `build/seal/analysis/wording_audit.json`.

7. **Parent table-placement work is in progress** — `scripts/prepare_seal_paper.py` and the main/AAAI rebuilds are being adjusted by the parent. This review did not edit those files; final manuscript tables and page counts may change. Supporting path: `scripts/prepare_seal_paper.py`.

## Recommendation

Regenerate `build/seal/budget_status.json`, rerun `python scripts/reproduce_all.py` and `python scripts/audit_seal_release.py`, and keep the wording upper-bound caveat visible before packaging or submission. No further edits were made by this review except this file.