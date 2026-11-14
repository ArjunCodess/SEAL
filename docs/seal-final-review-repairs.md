# SEAL final-review repairs

Scope: implementation follow-up to `docs/seal-final-independent-review.md`.
This repair pass used no inference calls, credentials, collection resumes,
frozen-data edits, commits, pushes, PRs, or submissions. The frozen study,
account-evidence, copied-source, five pinned `src/live*.py` modules, and
`src/integrity.py` files were not modified.

## 1. Release budget status rebuilt from frozen root ledgers

`build/seal/budget_status.json` had been overwritten by an offline pytest
collection and pointed at a temporary blind-study directory. It also omitted the
primary wording cohort and described a 150-credit combined wording envelope.

`scripts/audit_seal_release.py` now owns a release-time builder:

- `verified_release_ledger()` accepts only root ledgers under `data/`, verifies
  each frozen checksum manifest, requires zero pending episodes, requires a zero
  reservation balance, reconciles episode charges to `spent_total`, and checks
  the frozen request inventory.
- `build_budget_status()` counts exactly four roots once each. Wording
  workers/supervisors subledgers are reconciliation checks only, never extra
  stages.
- `run()` rebuilds the status before writing `release_audit.json`, so the final
  audit and the budget status cannot silently disagree.

The verified release inventory is:

| Stage | Root ledger | Calls | Committed credits |
|---|---|---:|---:|
| Blind provenance | `data/study_blind_provenance` | 240 | 1.29058114 |
| One gate retry | `data/study_gate_retry` | 114 | 0.54697127 |
| Wording diagnostic V2 | `data/study_wording_v2` | 504 | 1.43374151 |
| Wording primary V3 | `data/study_wording_v3` | 952 | 3.12142530 |
| **Total** | | **1,810** | **6.39271922** |

Envelope and balance wording is explicit in the generated document:

- the two wording cohorts share one **100-credit** ceiling, not 150;
- the **50-credit** development/collection-failure envelope is separate and
  unconsumed (no development root ledger exists);
- the **1,490-credit** authorization is the user-reported remaining ceiling, not
  an API-verified account balance;
- the historical **4.10737974** original-ledger spend is reference only and is
  not subtracted again;
- `pending_requests` is zero at both document and stage level, with
  `no_pending: true` and an explicit pending-check basis covering the root
  ledgers and wording phase subledgers;
- `release_total_check` records the 6.39271922 expected/observed match.

The document retains the `seal-budget-status-v1` schema and existing top-level
fields (`authorized_total_credits`, `historical_original_ledger_credits`,
`remaining_ceiling_for_new_stages_credits`, `allocated_envelopes_credits`,
`stage_envelopes_credits`, `unallocated_reserve_credits`, `stages`,
`committed_all_stages_credits`). Envelope names now distinguish the shared
wording ceiling from the separate development envelope.

`tests/conftest.py` redirects `seal_collection.BUILD_DIR` and
`seal_collection.BUDGET_STATUS_PATH` to a pytest temporary directory for every
test. Offline collection tests can therefore regenerate a mock status without
ever replacing the real release artifact.

## 2. Wording bounds now separate generation from attempted outcomes

`scripts/analyze_seal_followups.py` previously called planned-grid bounds
"`*_scheduled_*`" without saying that unavailable generation was included. The
scheduler still reports the same observed counts and completed-case rates; no
count or rate was edited by hand.

Two explicitly scoped bound pairs are now emitted:

| Field pair | Denominator | Unresolved treatment |
|---|---|---|
| `decision_generation_inclusive_lower/upper` | Planned 1,280-cell grid | Unavailable generation counts as zero unsafe in the lower bound and potentially unsafe in the upper bound |
| `decision_recorded_conditional_lower/upper` | Recorded inference attempts (completed + invalid + error) | Invalid/error outcomes remain unresolved: zero unsafe in the lower bound, potentially unsafe in the upper bound; missing generation is excluded |
| `decision_rate` | Completed valid decisions only | Unchanged |

The same pairs exist for `execution`. The legacy `decision_scheduled_lower/upper`
and `execution_scheduled_lower/upper` names remain as aliases of the
generation-inclusive pair, so existing tables keep their original numbers while
the new names say what the denominator includes.

`followup_analysis_manifest.json` now records the bound definitions, the legacy
aliases, the completed-only rate denominator, and the rule that invalid/error
calls are never counted as safe completions or silently dropped.

Tests cover three distinct cases: generation missing but attempted cells
complete, attempted cells missing because of invalid/error outcomes, and a zero
denominator. The last case yields `None` bounds and rates rather than a
fabricated zero.

## 3. Findings 3-6: caveats, not contract changes

**Finding 3 — original pairing is execution-only.** `pairing_coverage()` pairs
on `unsafe_execution` and `legitimate_completion` because that is the original
four-arm matching estimand from the frozen study plan. `unsafe_attempt` is a
decision endpoint and is intentionally not paired. The original paired counts
remain Gemma 2/4 in both attack strata, Qwen 0/4 single and 1/4 coordinated, with
`incident-timeline` unpaired in every attack stratum. Manuscripts must not claim
that unsafe-decision contrasts share this pairing coverage. Broadening to a
paired decision estimand would create a new contract and was not done.

**Finding 4 — honest controls are pooled across coordination within
model/arm.** All honest controls in the frozen protocol are three-speaker only,
so an attack cell has no matched coordination-specific honest cell. The pooled
`honest_by_model_arm` control is intentional and must stay labeled
three-speaker-only; it should not be presented as a matched single- versus
three-speaker contrast.

**Finding 5 — registered cohort paths fail closed.** The fixed
`data/study_wording_v3` / `data/study_wording_v2` paths and driver filenames in
`frozen_cohorts()` are provenance guards, not an accident. If a directory or
driver name changes, the audit refuses to infer a replacement cohort rather than
silently accepting different evidence. This is deliberate fail-closed behavior.

**Finding 6 — semantic validation remains limited.** Wording acceptance checks
schema, role, and supplied total tokens only. Zero exact copies are verified, but
there is no independent semantic annotation establishing linguistic diversity or
semantic adequacy. That limitation remains in `validation_scope` and the audit
JSON; it is not repaired by this pass.

## 4. Independent inventory retained

The audit still verifies: primary wording 320 worker calls with 201 accepted,
106 invalid, and 13 API-incomplete; 79 valid worker groups; 632 supervisor calls
with 565 completed, 23 invalid, and 44 JSON-decode errors; diagnostic wording 232
worker HTTP 429 failures and eight uncertain supervisor deliveries settled
without replay. Both endpoint definitions and the 280/280 accepted original
worker-copy audit remain independently verified. The original 960-call record is
preserved and was not rerun or repaired.

## 5. Tests and remaining concerns

Targeted tests cover the budget builder, explicit-only status writing, rejection
of non-`data/` roots, pytest output isolation, generation-inclusive versus
recorded-conditional bounds, invalid/error unresolved cells, and zero
denominators. Only targeted tests were run; no full rebuild, paper regeneration,
or final paper artifact overwrite was performed.

Remaining concerns: account balance remains user-reported rather than
API-verified; wording generation has no independent semantic annotation; the
parent's PDF/layout work is still in progress; and the release archive should be
rebuilt by the parent after this code change so generated manifests and package
contents reflect the repaired scripts.
