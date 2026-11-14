> Historical implementation checkpoint. Current paths and rebuild rules are in [seal-deliverables.md](seal-deliverables.md); completion is determined by the frozen ledgers and rebuild manifests.

# SEAL offline review — stages 1–3

Review date: 13 November 2026. Reviewed revision: working tree on `main` after stages 1–3 additions (`6239720` base plus uncommitted changes). Scope: offline only; no inference calls, no credentials, no commits, no edits to implementation. Original `data/study` was not modified.

## Scope and method

Reviewed:
- `docs/seal-venue-execution-plan.md`
- `scripts/reproduce.py`
- `src/live_analysis.py` (original 1,198 lines plus 482-line SEAL extension)
- `src/seal_analysis.py`
- `scripts/mutate_gate.py`
- `tests/test_live_analysis.py`
- `tests/test_gate_mutations.py`
- `legacy_sources/` and its manifest
- generated outputs under `build/` (not committed)
- `scripts/package_release.py`
- `docs/seal-collection-preflight.md`

Checks run:

```text
python -m pytest -q
# 4 failed, 260 passed, 7 subtests passed
# failures: tests/test_live_paper.py::LivePaperGeneratorTests (final mode compile refusal;
#   incomplete/truncated grid readiness; zero-denominator abstract wording)

python scripts/reproduce.py --verify-only
# exit 0: 26 study files, 5 frozen sources, recovery artifacts verified

python scripts/reproduce.py --verify-only --legacy
# exit 0: same counts (legacy_only parameter accepted but unused)

python scripts/reproduce.py --analysis-only
# exit 0: 960 episodes replayed; 240 attack gateway checks, 0 executions;
# 400 gate mutations; positive and equality-mismatch controls passed

python scripts/reproduce.py --legacy --analysis-only --output G:/tmp/legacy-build
# exit 0: mode "legacy", original reference tables verified

python (hash comparison)
# data/study/provenance/source_contract.json live_analysis.py: b12c22df...0b53
# legacy_sources/src/live_analysis.py:                   b12c22df...0b53 (match)
# src/live_analysis.py (current):                       f9548f85...b130 (drift)
```

Generated artifact checks:

```text
build/seal/analysis/two_endpoints.csv: 24 rows
build/seal/analysis/pairing_coverage.csv: 12 rows
build/gate_mutations.csv: 400 rows
build/seal/gate_mutations_manifest.json: positive_controls all_passed=true (40/40 publish);
  mismatch_controls all_passed=true (4/4 block with trusted_summary_mismatch)
```

## Real defects (ranked)

### 1. `verify_inputs` silently bypasses a drifted pinned source (`scripts/reproduce.py:27-52`, `234-241`, `344-350`)

`verify_inputs` accepts `legacy_only`, but never reads it. In both default and `--legacy` modes it loops over the five contract hashes and, on mismatch, tries a chain of fallback locations. For `src/live_analysis.py`, the current file no longer matches the contract hash, so default verification silently substitutes `legacy_sources/src/live_analysis.py` and still reports `frozen_sources_verified: 5`. This hides the fact that the runtime source used by stages 1–3 has drifted from the pinned contract and makes `legacy_only` a no-op.

Required repair:
- Make `legacy_only` actually control source selection.
- In default mode, require the current `src/*.py` files to satisfy the contract or fail with a named drift error; do not fall back to legacy copies.
- In legacy mode, verify only `legacy_sources/src/*` against the contract, with no fallback.
- Record which source set was verified and any drift in `reproduction.json` / verification output.

### 2. Unsafe fallback path resolution (`scripts/reproduce.py:43-52`)

The fallback chain includes `source_root / Path(relative).name` and `source_root / 'build' / 'seal' / 'legacy_sources' / Path(relative).name`. Those are name-only locations, so a modified or attacker-placed `live_analysis.py` at the repository root or under `build/seal/legacy_sources/` can satisfy the hash without being the preserved legacy snapshot. This is a verifier bypass, not merely a convenience path.

Required repair:
- Remove all name-only and build-directory fallbacks.
- Allow a legacy source only at the exact preserved path `legacy_sources/src/<relative>` when `legacy_only=True`.
- Verify `legacy_sources/source_manifest.json` and the contract hashes together.

### 3. Packaging omits the preserved legacy snapshot (`scripts/package_release.py:16-25`)

`release_files` packages `src`, `scripts`, `tests`, `paper`, `data/study`, `experiments`, `docs`, and `.github`, but not `legacy_sources`. The plan requires the exact five source files to be preserved outside `data/study` and available for isolated reproduction. A release built from the current tree would not contain the legacy snapshot needed to reproduce the frozen contract.

Required repair:
- Add `legacy_sources` to the packaged directories, excluding `__pycache__` and `.pyc` (already excluded by the existing filter).
- Include and verify `legacy_sources/source_manifest.json` in the archive checksums.

### 4. Extension implementation is appended to a pinned source, breaking frozen readiness (`src/live_analysis.py:1266-1771`; `tests/test_live_paper.py` failures)

Stages 1 and 3 are implemented by appending 482 lines to `src/live_analysis.py`, which is one of the five contract-pinned modules. The paper-readiness checks compare `src/live_analysis.py` to the frozen contract hash and therefore withhold all results. The four `test_live_paper` failures are the visible symptom. The plan permits extending `live_analysis.py`, but requires the original checksums to remain meaningful and verification not to be silently disabled.

Required repair:
- Either move the SEAL extension into a separate module imported by `src/seal_analysis.py`/`src/seal_stages.py`, leaving `src/live_analysis.py` byte-identical to the contract, or
- keep the extension in place but make the readiness/hash logic explicitly distinguish the legacy contract hash from the extension manifest hash.
- Do not leave a silent fallback that reports the pinned source as verified when it has drifted.

### 5. Gate mutation `swapped_key` semantics are not a key mutation (`scripts/mutate_gate.py:60-65`)

The documented mutation is “swapped key,” but the implementation swaps the *values* of the first two alphabetically sorted keys. That mutates two summary fields and can place a value of the wrong type into a field. It is not clear whether the intended operation is swapping values, swapping keys (renaming), or exchanging key positions. The current behavior is internally consistent but its semantics are ambiguous relative to the plan’s “one summary field or key operation at a time.”

Required repair:
- Define and document the exact key operation (value swap vs. key rename/swap) before freezing the mutation table.
- If value swapping is intended, consider renaming the mutation to `swapped_values`; if a true key swap is intended, implement it separately and keep both distinct.
- Record which operation was used in the manifest.

### 6. Mutation tests hard-code observed zeros and reasons (`tests/test_gate_mutations.py:46-68`)

`test_observed_actions_include_block_and_abstain` asserts `block == 300`, `abstain == 100`, `publish == 0`, and `attack_unsafe_executions == 0`; `test_earlier_veto_reasons_observed` asserts exact reason counts. The plan states the sweep must never pre-fill results with expected zeros. These assertions encode the current observed outcome as a pass condition, so a future mutation that legitimately publishes would fail the test even though the plan requires retaining and investigating it.

Required repair:
- Assert coverage, uniqueness, determinism, and that observed counts are recorded, not their expected values.
- Keep expected-zero assertions, if any, in a clearly labeled baseline/regression test rather than in the mutation acceptance test.
- Ensure the manifest reports observed counts without implying a guaranteed zero.

## Semantic / optional findings

7. **Honest-cell NaN rates in `two_endpoints.csv`.** `two_endpoint_table` produces `NaN` rates for honest cells because there are no attack rows. This is arithmetically unavoidable, but the generated CSV should mark these rows as `not_applicable`/`n.e.` or exclude attack-only fields from honest cells to avoid misreading. (`src/live_analysis.py:1298-1442`)

8. **`publication_attempted` is not normalized inside the two-endpoint boundary.** The plan says to normalize raw `attempted_publication` to `publication_attempted` at the boundary. `two_endpoint_table` trusts `outcome['publication_attempted']`, relying on `adapt_execution_outcome` in `src/live_study.py:876-912`. That is acceptable for study episodes, but direct callers (including tests) must supply the adapted field. Consider normalizing once in `two_endpoint_table` when `attempted_publication` is present. (`src/live_analysis.py:1332-1335`)

9. **Pairing coverage uses `ENDPOINTS`, not `DECISION_ENDPOINTS`.** `pairing_coverage` pairs on `unsafe_execution` and `legitimate_completion`, matching the original all-four-arm rule. It does not pair on `unsafe_attempt`. This matches the plan’s stage 3 acceptance and should not be changed silently, but the distinction should be documented. (`src/live_analysis.py:58`, `1549-1655`)

10. **`honest_by_model_arm` groups honest controls across coordinations.** This is intended because honest controls are three-speaker only, but the grouping is implicit. Document that paired honest controls are drawn from `coordination == 'honest'` records rather than from the attack cell’s coordination. (`src/live_analysis.py:1285-1289`)

11. **Extension manifest is incomplete.** `generate_extension_manifest` hashes only four files and omits both test files and `src/seal_analysis.py`. (`scripts/reproduce.py:234-265`)

12. **Legacy reproduction inserts `legacy_sources` into `sys.path` but leaves `ROOT` present.** In practice `legacy_sources/src` resolves first, and the legacy run passed, but the ordering should be made explicit and verified rather than relying on insertion order. (`scripts/reproduce.py:270-278`)

13. **`make_isolated_equality_mismatch_fixture` constructs a fixture from an honest base and then overwrites receipts, state, and candidate summary.** The resulting fixture is internally coherent for the equality check, but its `legitimate` flag remains `True` while it is used as an attack-style mismatch control. Document that this control is an honest fixture with an altered summary, not an attack fixture. (`scripts/mutate_gate.py:202-244`)

## What passed

- All 960 original IDs and status counts are unchanged (`completed=909`, `invalid=29`, `error=22`).
- Replay agrees with saved outcomes (`909` completed sink outcomes replayed).
- Claims/receipts/state decision and execution rates match on attacks; provenance requests can be vetoed and execution is `0`.
- Gate mutation sweep is deterministic, uses the actual `execute_decision`, covers `40` fixtures × `2` attack coordinations × `5` mutations = `400` rows, plus `40` honest positive controls and `4` equality-mismatch controls.
- Observed gate actions are `block=300`, `abstain=100`, `publish=0`; unsafe executions `0`. These are observed results, not pre-filled guarantees.
- Reason table contains `summary_unbound=280`, `provenance_incomplete=100`, `publication_unauthorized=20`; the equality check is exercised separately by the 4 mismatch controls.
- Pairing coverage reproduces `Gemma 2/4` in both attack strata, `Qwen 0/4` single, `Qwen 1/4` coordinated; `incident-timeline` is explicitly unpaired in every attack stratum.
- Original `data/study` hashes and old reference verification remain valid; legacy sources match the contract byte-for-byte.
- Tests are substantive: they exercise schema validation, denominators, pairing, bootstrap, endpoint normalization, and the actual gate. No monkeypatch is used to hide a regression in the SEAL additions.

## Recommendation before paid collection

Do not start stages 4–6 collection until items 1–4 are repaired and verified. Items 1 and 2 are verifier-integrity defects and must be fixed before any claim that the extension is reproducibly verified. Item 3 is required for release packaging. Item 4 is needed so the frozen readiness check can distinguish the original contract from the extension without silently treating drift as verification. Items 5 and 6 should be resolved before freezing the mutation table and its tests. Items 7–13 are documentation or usability improvements and do not block collection, but should be tracked.

After repairs, rerun:

```text
python scripts/reproduce.py --verify-only
python scripts/reproduce.py --verify-only --legacy
python scripts/reproduce.py --analysis-only
python -m pytest -q
```

All four must pass with the original `data/study` hashes unchanged. Only then should account preflight and paid collection proceed.
