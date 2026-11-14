# SEAL offline repairs

Repair implementation for `docs/seal-offline-review.md`. Scope is offline only; no
inference calls, no credentials, no commits, no push, no submissions. `data/study`
was not modified.

## Owned files

- `scripts/reproduce.py`
- `scripts/package_release.py`
- `src/live_analysis.py`
- `src/seal_analysis.py`
- `scripts/mutate_gate.py`
- `tests/test_gate_mutations.py`
- `tests/test_seal_analysis.py`
- `legacy_sources/`
- `docs/seal-offline-repairs.md`

## Finding resolutions

### 1. `verify_inputs` silent fallback removed

- Path: `scripts/reproduce.py::verify_inputs`
- Default mode now requires all five contract-pinned `src/*.py` files to match
  `data/study/provenance/source_contract.json`; on mismatch it raises
  `Frozen study source drift detected in <relative>`.
- No legacy, basename, root, or build-directory fallback remains.
- Return now records `source_mode`, `source_set`, `source_set_verified`,
  `verified_source_set`, `source_root`, and `legacy_manifest_verified`.

### 2. Unsafe fallback path resolution removed

- Path: `scripts/reproduce.py::verify_inputs`
- Legacy mode accepts only a directory named `legacy_sources` and verifies
  `legacy_sources/source_manifest.json` plus exact `legacy_sources/src/<relative>`
  bytes.
- Name-only and build-directory fallbacks were removed.

### 3. Packaging includes preserved legacy snapshot

- Path: `scripts/package_release.py::release_files`
- `legacy_sources` is included in packaged directories, with the existing
  `__pycache__` / `.pyc` exclusion.
- `legacy_sources/source_manifest.json` and all five
  `legacy_sources/src/*.py` files are present in the extracted archive.
- `scripts/package_release.py::main` verifies both current and legacy source sets.

### 4. Extension removed from pinned module

- Path: `src/live_analysis.py`
- Restored byte-for-byte from `legacy_sources/src/live_analysis.py`
  (SHA-256 `b12c22df699f92bac619ad1b4dd521f33615b0e66a0facff182d3a8770a03b53`).
- All SEAL extension logic now lives in `src/seal_analysis.py`, which imports
  frozen primitives from `src.live_analysis`.
- No appended extension code remains after `src/live_analysis.py::__main__`.

### 5. Gate mutation `swapped_key` semantics defined

- Path: `scripts/mutate_gate.py::apply_mutation`
- `swapped_key` is one deterministic key-rename operation: the first sorted key
  becomes `<key>_swapped`, preserving its value.
- Manifest records `swapped_key_operation` with before/after summaries and hashes.
- No two-field value swap is performed.

### 6. Mutation tests no longer hard-code attack acceptance

- Path: `tests/test_gate_mutations.py`
- Acceptance tests assert pair coverage/uniqueness, determinism, observed counts
  summing to the total, and manifest recording.
- Baseline observed counts are confined to the clearly labeled
  `test_baseline_observed_catalog_counts`.
- Positive controls and isolated equality-mismatch controls are asserted
  separately.

### 7. Honest 0/0 rates marked not applicable

- Path: `src/seal_analysis.py::two_endpoint_table`
- Honest-cell attack rates are `not_applicable`; honest completion/control rates
  are `not_applicable` when their denominator is zero.

### 8. Outcome normalization at boundary

- Path: `src/seal_analysis.py::normalize_outcome_boundary`
- Raw `attempted_publication` is adapted to `publication_attempted` once when
  present and the adapted field is absent.

### 9. Pairing endpoints and exclusions documented

- Path: `src/seal_analysis.py::pairing_coverage`
- Docstring documents pairing on `ENDPOINTS`
  (`unsafe_execution`, `legitimate_completion`) and exclusion of
  `unsafe_attempt` decision-only pairing.

### 10. Honest control grouping documented

- Path: `src/seal_analysis.py::two_endpoint_table`
- Comment documents that paired honest controls are drawn from
  `coordination == 'honest'` records.

### 11. Extension manifest includes production and tests

- Path: `scripts/reproduce.py::generate_extension_manifest`
- Hashes `src/seal_analysis.py`, `scripts/mutate_gate.py`,
  `scripts/package_release.py`, `tests/test_gate_mutations.py`, and
  `tests/test_seal_analysis.py`.
- Verifies the legacy manifest against the frozen contract.

### 12. Legacy imports isolated

- Path: `scripts/reproduce.py::reproduce` (legacy branch)
- Removes `src` and `src.*` modules, places `legacy_sources` first on
  `sys.path`, and asserts resolved files are under `legacy_sources`.

### 13. Equality-mismatch control documented

- Path: `scripts/mutate_gate.py::make_isolated_equality_mismatch_fixture`
- Documents that the control is an honest fixture with an altered summary, not
  an attack fixture.

## Verification

- `python scripts/reproduce.py --verify-only` passed:
  26 study files, 5 frozen sources, current source set.
- `python scripts/reproduce.py --verify-only --legacy` passed:
  26 study files, 5 frozen sources, legacy source set, manifest verified.
- `python scripts/reproduce.py --analysis-only` passed:
  960 episodes, 240 attack gateway checks, 0 executions, 400 gate mutations.
- `python scripts/reproduce.py --legacy --analysis-only --output G:/tmp/legacy-build2`
  passed in legacy mode.
- `python -m pytest -q` passed: 317 passed, 7 subtests passed.
- `python scripts/package_release.py` passed: archive verified, 665 files.
- `data/study` hashes unchanged.

## Test references

- `tests/test_seal_analysis.py`
- `tests/test_gate_mutations.py`
- `tests/test_live_paper.py`
- `tests/test_reproducibility.py`
