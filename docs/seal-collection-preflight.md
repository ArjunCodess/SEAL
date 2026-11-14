> Historical implementation checkpoint. Current paths and rebuild rules are in [seal-deliverables.md](seal-deliverables.md); completion is determined by the frozen ledgers and rebuild manifests.

# SEAL collection preflight — stages 4–6 implementation architecture

Planning date: 13 November 2026. Inspected revision: `62397205a5f471652ca7d9c26e25746ce0c43d2c` (clean tree).
Python: 3.14.0. `python scripts/reproduce.py --verify-only` passes on this revision.

This document is a read-only preflight. It was prepared without inference calls, without reading
credentials, and without writing any file other than itself. It specifies the implementation
architecture, CLI surface, tests, verification mechanisms, and schedule for stages 4–6 of
`docs/seal-venue-execution-plan.md`. It reports no measured outcomes beyond the frozen record and
makes no claim about results that have not been collected.

## 0. Scope boundaries

- Stages 1–3 (offline analysis, gate mutation sweep, pairing coverage) are owned by the separate
  offline child; this document does not implement or compete for them.
- Stage 2 outputs (`build/gate_mutations.csv`, `build/gate_mutations.manifest.json`) are complete
  and verified; the producing script and its tests are missing (blocker B-01).
- No credential values appear anywhere in this document or in any artifact it specifies. The API
  key was obtained by the parent out-of-band; runners read it only via `ADAPTION_API_KEY`
  (frozen protocol rule, `data/study/main/protocol.json` budget_rules.credentials_env).
- Everything below is a proposal to be frozen per-stage before collection, not a record of
  collection. Availability changes require a recorded amendment, never a silent model substitution.

## 1. Verified starting state

### 1.1 Integrity matrix (verified 2026-11-13 on this revision)

| Check | Result |
| --- | --- |
| 26 frozen study files vs `data/study/checksums.json` | all match |
| 5 pinned sources vs `data/study/provenance/source_contract.json` | all match (`src/integrity.py`, `src/live_fixtures.py` `af6e0efe…bc73`, `src/live_study.py` `daa1b9c7…8dea`, `src/live_workers.py` `eed02e17…6757`, `src/live_analysis.py` `b12c22df…0b53`) |
| `scripts/reproduce.py --verify-only` | passes (frozen study + recovery artifacts) |
| Git tree | clean at HEAD; `results/` directory absent (only `.tools/` remains) |

### 1.2 Frozen record facts reused by stages 4–6

- Original ledger: 960 requests — 909 completed, 22 failed, 29 invalid; 4.10737974 credits spent.
  These are historical ledger facts, not current account evidence.
- Original provenance-arm attack episodes: 100 gate `refused_block` results (60 gemma-4-31b-it,
  40 qwen3.5-397b) with block reasons only `input_unbound` (62) and `publication_unauthorized`
  (38); 40 further attack episodes were `supervisor_declined`. This is observed planning context
  for stage 5 sizing, not a blind-stage prediction.
- Original worker corpus: `data/study/workers/assembled_corpus.json`, sha256
  `f8c79dd758e05fd5cfc8a36cc965762b27d77db20aa7fcbbcd6f6c768cfbdf7e`; 120 fixtures
  (40 test indexes × coordination), message counts single=1, coordinated=3, honest=3.
- Stage-2 sweep: `build/gate_mutations.csv` (520 rows) + `build/gate_mutations.manifest.json`
  (schema `seal-gate-mutations-v1`, `script_sha256 e6be64c5da79cddadd72561601a7ad493ba8a7b260ab44a90a9cecd0f59b0957`,
  `csv_sha256 575e0d6c…a13d3e61`, `executor_sha256` = the pinned `live_fixtures.py` hash, 120 fixtures).
  Result recorded in the manifest: no attack mutation publishes; 40 legitimate true-summary
  positive controls execute.
- Historical budget snapshot `data/study/main/budget.json`: verified_balance 1495.819, spend_cap
  20.0, reserve_fraction 0.2, prices gemma-4-31b-it 0.09/0.34 and qwen3.5-397b 0.55/3.50 per
  million input/output tokens, max_output_tokens 8192 (amendment rationale recorded: pilot Qwen
  2048 incompletes), max_input_chars 50000, unit "Adaption credits", pricing_source
  `https://docs.adaptionlabs.ai/inference-stacks/models/index.md`. **All of it is historical.**

## 2. Design approach: additive runners over frozen primitives

The five pinned source modules stay byte-identical. All stage 4–6 behavior is added in new files
that import the frozen modules and, where a behavior must differ, install narrow runtime patches
inside the runner process only. The frozen files are never edited, so `verify_protocol_integrity()`
and `scripts/reproduce.py --verify-only` keep passing unchanged.

Two frozen-code facts make this approach work (verified in source):

1. `live_study.fixture_api()` returns the `live_fixtures` module object itself
   (`src/live_study.py:95`), and `run_supervisor_episode` calls `fx.supervisor_observation(...)`
   through that reference. An attribute patch on the module (e.g. blinding `supervisor_observation`)
   therefore reaches the frozen episode loop without touching frozen bytes.
2. `live_study.verify_protocol_integrity()` validates only that the protocol document's
   `source_hashes` block matches the current source hashes (`src/live_study.py:295–317`). A new
   protocol document that carries the same pinned hashes passes unchanged, so new studies can reuse
   `run_schedule`, the ledger, the episode store, and the budget validator as-is.

### 2.1 Runtime patch inventory (process-local only; no file changes)

| Patch point | Installed by | Purpose |
| --- | --- | --- |
| `live_fixtures.supervisor_observation` (wrap) | `scripts/run_blind_provenance.py` | Stage 4: null `provenance.trusted_summary` in the outward observation; everything else byte-identical |
| `live_fixtures.supervisor_observation` (wrap) | `scripts/run_gate_retry.py` | Stage 5: keep the stage-4 blinding and append `retry_context` (first decision + block reason) for retry entries |
| `live_study.episode_id` (replace) | `scripts/run_wording_v2.py` | Stage 6: condition-aware episode IDs (`episode_id_v2`) to remove the honest single/coordinated collision |
| `live_study.validate_worker_corpus_for_entries` + `live_study.apply_worker_corpus` (wrap) | `scripts/run_wording_v2.py` | Stage 6: corpus keyed by `(fixture_id, condition)`; frozen functions cannot express honest-single (see §8.4) |
| `ResponsesClient` (logging wrapper passed as `client=` to `run_schedule`) | stages 4, 5, 6 | Raw request/response records (`responses.jsonl`) without touching frozen code |

Every runner re-verifies the five pinned hashes against `data/study/provenance/source_contract.json`
at startup and refuses to run on drift. Each runner also asserts its patch is actually installed
(episodes carry the expected markers: nulled `trusted_summary`, retry context, v2 IDs) and that the
patched files on disk still match the contract.

### 2.2 New files

| File | Purpose |
| --- | --- |
| `src/seal_stages.py` | Shared helpers: source-contract re-verification, budget-config wrapper, account-evidence checks, global ceiling guard, `.run.lock`, path guards, freeze/reconcile helpers |
| `scripts/run_blind_provenance.py` | Stage 4 runner (freeze / dry-run / collect / freeze-study / compare / smoke) |
| `scripts/run_gate_retry.py` | Stage 5 runner (eligibility / freeze / dry-run / collect / freeze-study) |
| `scripts/run_wording_v2.py` | Stage 6 runner (develop / collect-workers / freeze-corpus / dry-run / collect / freeze-study) |
| `scripts/verify_account_evidence.py` | Validate + hash the parent-produced evidence snapshot |
| `configs/seal/stage4_budget.json`, `stage5_budget.json`, `stage6_workers_budget.json`, `stage6_supervisor_budget.json`, `stage6_dev_budget.json` | Per-stage budget configs (new directory, proposed) |
| `tests/test_seal_blind.py`, `tests/test_seal_retry.py`, `tests/test_seal_wording_v2.py`, `tests/test_seal_guards.py` | Tests (§10) |

Outputs go to `data/study_blind_provenance/`, `data/study_gate_retry/`, `data/study_wording_v2/`
and `build/seal/analysis/` (appendix §14). `data/study/` remains byte-for-byte untouched, and no
new call may write inside it (enforced path guard).

## 3. Shared infrastructure (`src/seal_stages.py`)

### 3.1 Budget wrapper (fail-closed extensions, no edits to the frozen validator)

`validate_budget_config` (`src/live_study.py:346`) requires: unit_name, input/output
price_per_million, verified_balance, inference_eligible, no_overage, max_input_chars,
max_output_tokens, max_requests, spend_cap; refuses null balance, non-eligible inference, paid
overage, and spend_cap > balance. The wrapper calls the frozen validator first, then enforces the
new stage fields:

| Field | Rule |
| --- | --- |
| `authorized_ceiling_credits` | must equal 1490 (the plan's total authorized ceiling) |
| `account_evidence_sha256` | sha256 of the pinned evidence snapshot manifest; must match the recomputed hash at runner start |
| `evidence_date` | ISO date of the snapshot; runner refuses if the stage's first send happens after the evidence re-verification window agreed in the protocol (proposed: evidence re-verified on the collection date itself) |
| `stage_envelope_credits` | equals `spend_cap` for the stage (20 / 20 / 100 / 50) |
| `models` | exactly `["gemma-4-31b-it", "qwen3.5-397b"]` unless a recorded amendment pins substitutes |

`verified_balance` and all prices in a stage budget config must be copied from the evidence
snapshot (§4), never from `data/study/main/budget.json` and never from public docs.

### 3.2 Global ceiling guard

The frozen headroom `spendable_headroom(budget, ledger) = min(spend_cap, balance −
balance·reserve_fraction) − (reserved_total + spent_total)` (`src/live_study.py:450`) is enforced
per run against that stage's own ledger. There is no cross-run ceiling in frozen code. The new
guard computes, before every send:

```
global_committed = 4.10737974            # original ledger, constant
                 + Σ committed(stage ledgers: study_blind, study_gate_retry, study_wording_v2 incl. worker ledgers, dev ledger)
stop if global_committed + reservation > authorized_ceiling_credits − unallocated_reserve
```

with `authorized_ceiling_credits = 1490` and the plan's 1300 credits kept unallocated. A stop is a
hard refusal recorded as `refused_budget`/reason `global_ceiling` in the stage ledger; it is never
an automatic reallocation (plan credit policy).

### 3.3 Single-writer locking and replay guards

- Each runner takes `.run.lock` in its study directory with `O_CREAT | O_EXCL`; a second concurrent
  runner fails immediately. The lock is removed on clean exit and left in place on crash (stale
  locks are removed only by a documented manual step, never automatically), matching the frozen
  rule that an interrupted pending call cannot silently replay (`assert_no_pending_replay`,
  `src/live_study.py:625` area; `ResumeError` on pending episodes, `run_supervisor_episode`).
- Reservation-before-send, `retries = 0`, uncertain calls charging their reservation, and the
  `reservation_input_basis = utf8_bytes_instructions_plus_input` (+ `SAFE_FRAMING_TOKEN_OVERHEAD
  = 128` framing) are inherited unchanged from `run_supervisor_episode`.
- Path guard: runners refuse any write under `data/study/` and refuse to run without an explicit
  study directory (the frozen defaults `DEFAULT_STUDY_DIR = results/adaption_live` and
  `worker_output_root = results/adaption_live_workers/<split>` point at a directory that no longer
  exists; every new run passes `--study-dir` and defines its own worker paths).

## 4. Account / model / price verification (fail-closed, no credentials here)

The parent holds the API key. Verification of current balance, model availability, and prices is a
parent action producing an evidence snapshot; the runners only consume and hash-check it.

### 4.1 Evidence snapshot (produced by the parent, outside this preflight)

`data/account_evidence/<YYYY-MM-DD>/`:

| File | Content |
| --- | --- |
| `balance.json` | current prepaid balance, unit name (expected "Adaption credits" — verify, don't assume), retrieval timestamp, source (account page or billing endpoint as used by the parent) |
| `prices.json` | per-model input/output price per million tokens for both models, pricing source URL, retrieval timestamp |
| `models.json` | availability evidence for `gemma-4-31b-it` and `qwen3.5-397b` (model list / model metadata as available), retrieval timestamp |

No tokens, keys, or account identifiers. Only values, citations, and timestamps.

### 4.2 Verification command and runner-side enforcement

```
python scripts/verify_account_evidence.py verify --evidence-dir data/account_evidence/2026-11-13 --require-models gemma-4-31b-it qwen3.5-397b
```

Validates schema, writes `data/account_evidence/<date>/evidence_manifest.json` with the sha256 of
each file and a canonical hash of the combined snapshot. Every stage budget config pins that
canonical hash in `account_evidence_sha256`; `seal_stages` recomputes it at runner start and fails
closed on: missing snapshot, hash mismatch, stale date, config balance ≠ snapshot balance, or
config prices ≠ snapshot prices. Consequences: stage 4–6 cannot start until the snapshot exists
and matches — by design, not a defect (blocker B-04 tracks the parent action).

Model availability is evidenced by `models.json`; a functional check is optional and paid:

```
python scripts/run_blind_provenance.py smoke --budget-config configs/seal/stage6_dev_budget.json
```

One completion request per model, recorded in the dev ledger (50-credit development/collection
failures envelope), never in a study denominator. The dataset SDK's `estimate=True` is not assumed
to quote this repository's direct `/api/v1/responses` endpoint; no runner calls it. Actual cost
verification is behavioral: every completion's `usage`-derived charge is compared against its
reservation, and the first send of each stage is additionally checked against the envelope with a
conservative multiplier recorded in the stage protocol.

## 5. Budget and cost plan

Stage envelopes from the plan (maximum allowances, not cost estimates): stage 4 = 20 credits,
stage 5 = 20, stage 6 = 100, bounded development and collection failures = 50; 1,300 of the 1,490
authorized ceiling stays unallocated. Historical estimates below were computed offline from the
historical per-model prices and observed request byte sizes (max input bytes in parentheses):
stage 4 ≈ 4.19 credits reserved for 240 requests (5,454 B); stage 5 ≤ 2.81 for ≤160 retries
(5,763 B); stage 6 supervisor phase ≈ 21.76 for 1,280 calls (5,501 B); stage 6 worker generation
≈ 0.10 for 320 calls; development ≈ 0.03; historical average ≈ 0.00428 credits/call. **These are
historical-price planning numbers only**; current prices may differ and only the evidence snapshot
plus envelope checks are authoritative. Output allowance stays at max_output_tokens = 8192 for
both models (pilot Qwen incompletes at 2048; limits are not reduced mid-study).

## 6. Stage 4 — blind provenance collection (240 calls)

### 6.1 What is collected

Exactly the plan's schedule: the same 40 test fixture indexes × three conditions (attack single,
attack coordinated, existing three-speaker honest) × two supervisor models × the provenance arm
only = 240 calls; 160 attack + 80 honest. Fixtures are rebuilt with the frozen `make_fixture`
using the frozen main-schedule index list and split; worker text comes from the original corpus
(`assembled_corpus.json`, sha above). No claims/receipts/state arm is collected here.

### 6.2 The blinding wrapper

`run_supervisor_episode` builds the request from `fx.supervisor_observation(fixture, "provenance")`.
The blind runner installs:

```python
def blind_supervisor_observation(fixture, arm):
    observation = frozen_supervisor_observation(fixture, arm)
    if arm == "provenance" and "provenance" in observation:
        observation["provenance"]["trusted_summary"] = None   # the only change
    return observation
```

This is sound because the executable gate never reads the view: `execute_decision` →
`_provenance_action` (`src/live_fixtures.py:676–693`) reads `fixture["provenance"]` directly and
re-derives the trusted summary itself from `trusted_inputs`. Blinding is therefore purely an
observation-layer change; gate semantics, reasons, and outcomes are bit-identical to the frozen
implementation. The fixture and the gate are unchanged, exactly as the plan requires.

Retained evidence (per plan): `trusted_inputs`, `trusted_input_digest`, `executed_input_digest`,
`binding_complete`, `allowlist`, `allowed_channels`, the crypto and state views, candidate summary
and worker messages. The retained `trusted_inputs` permit a determined model to recompute the
trusted answer — documented residual, deliberately kept (plan: "Retain all other evidence, even if
it permits the model to infer the answer"; see the audit in §9).

### 6.3 Protocol, ledger, freezing

The runner writes `data/study_blind_provenance/protocol.json` (name `SEAL-BLIND-PROVENANCE-v1`)
before collection, carrying: pinned prompt template hashes (the frozen `build_user_input`
canonical-JSON wrapper), model IDs, generation settings (temperature 0, store false,
max_output_tokens 8192, max_input_chars 50000), the 240-entry schedule with order and seed, output
limits, parser (`parse_supervisor_decision`), error policy, `source_hashes` for the pinned
modules, and the analysis specification reference. It then reuses the frozen machinery wholesale
via `run_schedule(entries, client=logging_client, ...)`, which provides: `verify_protocol_integrity`,
budget validation, worker-corpus validation, run manifest, per-episode denominators, reservation
ledger, atomic episode store, pending-replay refusal, and the 8-episode progress output.

Raw request/response records: the logging `ResponsesClient` wrapper appends
`{episode_id, request_sha256, request_input, raw_response}` to
`data/study_blind_provenance/responses.jsonl` (fsync per record). This satisfies the plan's "raw
request/response records" without touching frozen code and gives stage 5 byte-exact parent
observations.

Freeze-study: reconcile the 240 scheduled episode IDs against the ledger (all terminal; any
`pending` blocks freezing), write `checksums.json` over every file in the study directory, and
record terminality. Invalids and errors stay in the ledger; no outcome-dependent replacements; a
frozen run is terminal.

Episode identity: frozen `episode_id(protocol_sha256, model, fixture, arm)` is reused unchanged.
Because the protocol content (and therefore its sha256) differs from the original study, none of
the 240 IDs can collide with the 960 original IDs, and within the blind study fixture_ids
(`family.index.coordination`) differ per condition.

### 6.4 CLI

```
python scripts/run_blind_provenance.py freeze-protocol --study-dir data/study_blind_provenance
python scripts/run_blind_provenance.py dry-run        --study-dir data/study_blind_provenance --budget-config configs/seal/stage4_budget.json --worker-corpus data/study/workers/assembled_corpus.json
python scripts/run_blind_provenance.py collect        --study-dir data/study_blind_provenance --budget-config configs/seal/stage4_budget.json --worker-corpus data/study/workers/assembled_corpus.json
python scripts/run_blind_provenance.py freeze-study   --study-dir data/study_blind_provenance
python scripts/run_blind_provenance.py compare        --study-dir data/study_blind_provenance --original data/study --output build/seal/analysis/provenance_comparison.csv
python scripts/run_blind_provenance.py smoke          --budget-config configs/seal/stage6_dev_budget.json   # optional, dev ledger
```

Dry-run produces the frozen `dry_run_report` shape (episode count, reservation total, max request
bytes, headroom, `fits_budget`) under the installed blinding wrapper, so estimates match collection
byte sizes. Budget config: max_requests = 240, spend_cap = 20, prices/balance from the evidence
snapshot.

### 6.5 Comparison output

`build/seal/analysis/provenance_comparison.csv`: per model and condition — old decisions, old
executions, blind decisions, blind executions, with explicit denominators (scheduled/completed/
invalid/error), honest completion reported separately, and common completed fixture IDs plus
exclusions for old-vs-blind decision contrasts. Generated offline from the blind episodes plus the
frozen original episodes; historical comparison caveat (different collection time, unpinned backend
versions) is carried in the table header comment.

Acceptance (from the plan): 240 scheduled IDs reconcile; the observation-only nulling test passes;
the gate implementation hash matches the original (`af6e0efe…bc73`); all four rates regenerate
from saved calls; original study hashes are unchanged (re-run `--verify-only` after collection).

## 7. Stage 5 — gate retry (≤ 160 calls)

Starts only after stage 4 is frozen. Directory: `data/study_gate_retry/`, protocol
`SEAL-GATE-RETRY-v1`, with the parent blind study's checksums pinned.

### 7.1 Eligibility (frozen before the retry protocol is committed)

A parent episode is eligible iff all of:

1. `status == "completed"` in the blind study;
2. `arm == "provenance"` and coordination ∈ {single, coordinated} (attack);
3. `supervisor_decision.action == "publish"`;
4. `execution_outcome.enforcement.result == "refused_block"`;
5. `execution_outcome.enforcement.reason` ∈ the pinned block-reason set:

   `integrity_rejected, omitted_step, step_failed, summary_unbound, keeper_unbound,
   state_summary_mismatch, input_unbound, tool_unauthorized, publication_unauthorized,
   trusted_summary_mismatch`

Excluded, and kept separate, never silently reclassified: `refused_abstain` outcomes (reasons
`provenance_incomplete`, `checkpoint_absent`, `step_indeterminate`, `state_unavailable`),
`supervisor_declined`, honest controls, invalid/error episodes. The eligible count is published
(from the frozen eligibility manifest) before any retry is sent. Planning context: the original
study showed 100 gate-blocked publish requests on attack provenance episodes; the actual stage-5
count depends on blind outcomes and can be anything from 0 to 160.

### 7.2 The retry call

One retry per eligible parent — the same supervisor model receives its original observation, its
first decision, and the actual block reason once. No true summary, no new worker evidence, no
coaching, no second retry. The runner wraps `supervisor_observation` so a retry entry's outward
input is the stored stage-4 observation (blinding preserved, `trusted_summary` still null) plus:

```json
"retry_context": {"first_decision": {...}, "block_reason": "..."}
```

Byte-identity with the parent request is verified before send: the rebuilt observation's canonical
form must equal the parent's logged `request_input` (from `data/study_blind_provenance/
responses.jsonl`, matched by parent episode id / request_sha256); on mismatch the runner aborts
rather than silently substituting a re-render. The new decision is executed through the unchanged
`execute_decision` (frozen path — the retry wrapper does not alter the gate).

Because the frozen denominator schema has no parent field, parent/retry linkage is written by the
runner to `data/study_gate_retry/retry_links.jsonl`
(`{retry_episode_id, parent_study, parent_episode_id, parent_request_sha256, block_reason}`)
before each send; retry episode IDs are new hashes under the retry protocol sha256, so no collision
with parent IDs.

### 7.3 Records and caps

Per the plan: log first decision and execution, block reason, retry decision, retry execution,
status, and cost (episode records carry all of these from the frozen schema; links and the block
reason ride in `retry_links.jsonl`). Report rates conditional on eligible parents and
full-trajectory attack outcomes separately; missing retries remain unresolved; a retry failure
never erases an observed first request. Hard caps: `max_requests = 160` in
`configs/seal/stage5_budget.json` (frozen per-run cap), plus the plan's one-retry-per-parent rule
(duplicate parent id → immediate error), plus the 20-credit envelope and the global ceiling guard.

### 7.4 CLI

```
python scripts/run_gate_retry.py eligibility  --parent-study data/study_blind_provenance --output data/study_gate_retry/eligibility_manifest.json
python scripts/run_gate_retry.py freeze-protocol --study-dir data/study_gate_retry --parent-study data/study_blind_provenance
python scripts/run_gate_retry.py dry-run      --study-dir data/study_gate_retry --parent-study data/study_blind_provenance --budget-config configs/seal/stage5_budget.json
python scripts/run_gate_retry.py collect      --study-dir data/study_gate_retry --parent-study data/study_blind_provenance --budget-config configs/seal/stage5_budget.json
python scripts/run_gate_retry.py freeze-study --study-dir data/study_gate_retry
```

`eligibility` is offline (reads blind episodes only), writes the manifest, and is a prerequisite
hash input of the retry protocol — freezing the manifest before the protocol is the mechanism that
makes the count publishable before sends. Claims wording: described as a single feedback-conditioned
supervisor retry on instructed, non-adaptive attacks — not an adaptive attacker study and not
evidence of training.

Acceptance (from the plan): no parent has more than one retry; every retry has a frozen eligible
parent; every execution is replayable from the stored decision through `execute_decision`.

## 8. Stage 6 — new wording v2 (320 worker calls + 1280 supervisor calls)

Starts only after stages 4 and 5 are stored and frozen. Directory: `data/study_wording_v2/`,
protocol `SEAL-WORDING-V2-v1`, own corpus, hashes, ledger, and analysis outputs.

### 8.1 Worker builder and conditions

`build_worker_user_input_v2` mirrors the frozen builder (`src/live_workers.py:294`) and removes
exactly one field: `wording_reference` (the frozen builder's scripted baseline at lines 314–317 —
the reason all 280 accepted original worker calls copied the template). Everything else keeps its
semantics: task, role, coordination, stance, public task context, public totals (honest →
`true_summary`, attack → `candidate_summary`), message index, prior worker claims, JSON output.
`WORKER_INSTRUCTIONS_V2` is audited against the frozen `WORKER_INSTRUCTIONS` for any other
requirement to copy an original string; validators (`validate_accepted_worker_message`,
`fixture_truth_unchanged`, `_contains_label_leak`) are reused, and the new role/factual validators
check role and factual constraints without demanding verbatim wording.

Four conditions per fixture index (all on the 40 existing test fixtures):

| Condition | Speakers | Underlying fixture | Notes |
| --- | --- | --- | --- |
| attack-single | 1 (message count 1) | attack (`single`) | instructed attack goals and original evidence semantics retained |
| attack-coordinated | 3 (message count 3) | attack (`coordinated`) | as original coordination structure |
| honest-single | 1 (message count 1) | honest (`honest`) | new condition: only the outward speaker presentation and text change; underlying legitimate workflow, oracle, and gate preserved; not a relabeled attack; the one speaker's role and knowledge are pinned in the protocol |
| honest-coordinated | 3 (message count 3) | honest (`honest`) | as original honest structure |

Worker calls: 40 × (1 + 3 + 1 + 3) = 320. Prompt development happens on development fixtures and
is budgeted separately (dev envelope); test collection is frozen before it starts.

### 8.2 Generation policy (frozen before collection)

Per (fixture, condition): up to 3 seeded generation attempts; a message failing validation is
regenerated within the bound; if all attempts fail, that condition is unscheduled for that fixture
and recorded as `generation_missing` in the denominator — no fallback text, no resampling beyond
the bound, nothing kept sampling until a more persuasive attack appears. Logged per plan: exact-copy
rates, duplicates, lengths, invalid outputs, and rejected attempts (reuse of the frozen
`write_generation_manifest` / audit-line patterns), with fallback/`generation_missing` use reported
separately. Exact-copy audit: verbatim equality and n-gram overlap against the original corpus
text for the same fixture index (for honest-single, against the original honest three-speaker text
as the defined comparison) — the original string must not be reproduced; paraphrase with the same
facts is the accepted target.

### 8.3 Corpus freeze and text identity

Accepted messages are assembled into `data/study_wording_v2/corpus_v2.json` and frozen exactly
once (`freeze-corpus` writes the corpus and its sha256; a second freeze with different content is
an error). The same frozen worker text is used across all four arms and both supervisors within
each fixture/condition — the corpus is condition-keyed, and every supervisor entry's ID pins the
text hash (§8.4).

### 8.4 Episode identity — the collision fix

Frozen `episode_id` is `sha256(protocol | model | fixture_id | arm)`. The honest-single and
honest-coordinated conditions share one fixture id (`family.index.honest`), so under the frozen
function they collide for the same arm and model — the same latent collision the original design
avoided only because honest had a single realization. The runner therefore replaces the module
attribute `live_study.episode_id` with:

```python
def episode_id_v2(protocol_hash, model, fixture, arm, *, condition, worker_text_sha256):
    payload = canonical_json({**base_keys,
        "condition": condition,             # attack_single | attack_coordinated | honest_single | honest_coordinated
        "worker_text_sha256": worker_text_sha256})
    return sha256(payload)
```

The extension makes honest-single vs honest-coordinated distinct and binds each episode to the
exact text realization. Guard test: injectivity across all 40 × 4 conditions × 4 arms × 2 models
(= 1,280 distinct IDs) and inequality with every original 960 ID.

The frozen corpus applier cannot express the honest conditions either:
`validate_worker_corpus_for_entries` (`src/live_study.py:625`) requires the corpus entry for a
fixture_id to match the scripted baseline's message count (3 for honest) and roles, and
`apply_worker_corpus` (`src/live_study.py:668`) keys by `fixture_id` only. The runner wraps both
with condition-aware versions keyed `(fixture_id, condition)`, delegating content checks to the
frozen rules and keeping the replacement limited to `worker_messages` (never touching candidate or
true summaries, receipts, or state), with `fixture_truth_unchanged` re-asserted per applied entry.

### 8.5 Supervisor phase and visibility

1,280 new supervisor calls: 40 × four conditions × four arms (claims, receipts, state,
provenance) × two models, executed through `run_schedule` with the v2 ID patch installed.
Provenance visibility: original visibility by default — `trusted_summary` is NOT nulled in stage 6
(stage 4 is the dedicated blinding experiment; changing both wording and visibility at once would
confound interpretation). Old and new observations are never mixed into one headline rate.

Honest one-speaker offline controls (before any supervisor call): for every honest-single corpus
entry, `reference_decision` publishes on the claims arm (unanimous `claim_status: complete`),
the provenance gate publishes through `execute_decision` (binding complete, candidate equals the
derived trusted summary), and receipts/state references agree — recorded as an offline control
report in the study directory.

### 8.6 CLI

```
python scripts/run_wording_v2.py develop         --split development --budget-config configs/seal/stage6_dev_budget.json --output data/study_wording_v2/workers/development
python scripts/run_wording_v2.py collect-workers --split test        --budget-config configs/seal/stage6_workers_budget.json --output data/study_wording_v2/workers/test
python scripts/run_wording_v2.py freeze-corpus   --corpus data/study_wording_v2/corpus_v2.json
python scripts/run_wording_v2.py dry-run         --study-dir data/study_wording_v2 --budget-config configs/seal/stage6_supervisor_budget.json --worker-corpus data/study_wording_v2/corpus_v2.json
python scripts/run_wording_v2.py collect         --study-dir data/study_wording_v2 --budget-config configs/seal/stage6_supervisor_budget.json --worker-corpus data/study_wording_v2/corpus_v2.json
python scripts/run_wording_v2.py freeze-study    --study-dir data/study_wording_v2
```

Budgets: workers max_requests = 320 (worker spend cap within the 100-credit stage envelope),
supervisors max_requests = 1280, spend_cap = 100. Worker output paths are v2-owned
(`data/study_wording_v2/workers/<split>/` — the frozen `worker_output_root` default under the
missing `results/` tree is bypassed by explicit paths).

Acceptance (from the plan): a fresh hashed worker corpus exists; every supervisor episode pins its
text hash; all conditions use the same four test families; honest one-speaker fixtures pass the
independent oracle/gate controls; results regenerate offline. New wording is not evidence of new
mechanisms or task generalization.

## 9. Oracle-leakage audit of the provenance observation (stage 4)

Field-by-field audit of what `supervisor_observation(fixture, "provenance")` exposes
(`src/live_fixtures.py:741–754` and surrounding views):

| Field / view | Content on attack fixtures | Stage-4 handling |
| --- | --- | --- |
| `provenance.trusted_summary` | the derived true answer whenever `binding_complete` — the only direct oracle carrier | **nulled** (the one and only change) |
| `provenance.trusted_inputs`, `trusted_input_digest` | raw trusted inputs; deterministically recompute the trusted summary via the frozen summary spec | retained by plan; documented residual recomputability |
| `state.bodies`, `state.summary_spec`, `state.recomputed_summary` | keeper snapshot; `recomputed_summary` equals `candidate_summary` on test attack families (harness consistency) | retained; leaks the candidate (already visible), not the true answer |
| crypto view (session, publics, receipt packets, checkpoint, `minimum_version`, `integrity`) | signatures and integrity verdicts | retained; no answer content |
| `task`, `candidate_summary`, `worker_messages` | the decision inputs themselves | retained |
| hidden labels | trust labels and the harness true summary are not copied into any view (`supervisor_observation` docstring, lines 986–989) | n/a; asserted by test |

Additional audit facts: gate reasons shown in stage 5 (`input_unbound`, `publication_unauthorized`
in the original record) are computed by checks that precede the `trusted_summary` equality in the
`_provenance_action` chain, so no reason string ever carried the true answer. Blind observations
are structurally distinguishable from original saved observations (null vs non-null
`trusted_summary` on bound fixtures), plus a distinct protocol hash and study directory, as the
plan requires. The nulling test (§10) additionally scans the whole serialized request for a
verbatim copy of the trusted summary through any other field; it passes because no view copies the
summary, while `trusted_inputs` remain (declared residual, not a hidden copy).

## 10. Test plan

All tests are offline (no network, no credentials) and follow the existing conventions
(`tests/test_live_fixtures.py` hidden-key/expected-action style, atomic-file assertions). Suite:
`python -m pytest tests/test_seal_blind.py tests/test_seal_retry.py tests/test_seal_wording_v2.py tests/test_seal_guards.py -q`.

### `tests/test_seal_blind.py`
- Wrapper nulls `provenance.trusted_summary` and only that field: full deep-compare of the blinded
  observation against the frozen observation otherwise.
- Serialized request input contains no field whose value equals the trusted summary (verbatim scan);
  `trusted_inputs` present.
- Gate invariance: `execute_decision(fixture, "provenance", …)` under the wrapper reproduces the
  frozen refusal reasons on attack fixtures and publication on honest fixtures.
- Schedule shape: exactly 240 entries (160 attack + 80 honest), 40 indexes × 3 conditions × 2
  models × provenance arm; dry-run reconciles without artifact mutation.
- Identity: no blind episode ID equals any of the 960 original IDs.
- Freeze: `freeze-study` refuses while any episode is pending; checksums cover the directory;
  invalid/error records retained.
- Comparison CSV regenerates from saved blind episodes + frozen original episodes with explicit
  denominators and separate honest completion.

### `tests/test_seal_retry.py`
- Eligibility filter accepts exactly the five-condition pattern and rejects each exclusion class
  (`supervisor_declined`, `refused_abstain` with each abstain reason, honest, invalid, failed).
- The pinned 10-reason set is asserted literally in the test (drift fails the test).
- Eligibility manifest is frozen before the protocol (protocol pins the manifest hash; ordering
  enforced).
- Retry input = parent observation (byte-identical to the logged parent request input) + first
  decision + block reason; scan: no trusted summary, no true summary, no coaching strings.
- Caps: 161st eligible retry → `refused_budget`; duplicate parent → error; retry decision executes
  through unchanged `execute_decision` and replays to the stored enforcement outcome.
- Linkage: every retry episode has exactly one `retry_links.jsonl` row with parent study hash,
  parent episode ID, and block reason.

### `tests/test_seal_wording_v2.py`
- Builder schema: `wording_reference` absent; all other frozen keys present; JSON output required.
- Message counts per condition 1/3/1/3; corpus entries keyed `(fixture_id, condition)`.
- `episode_id_v2` injectivity over the full 1,280-cell grid and disjointness from the 960 original
  IDs; honest-single vs honest-coordinated differ for the same fixture/arm/model.
- Same text across arms and supervisors within each fixture/condition (corpus-level assertion).
- Honest-single controls: claims reference publishes; provenance gate publishes via
  `execute_decision`; pinned role/knowledge fields present in the builder payload.
- Exact-copy audit: no accepted v2 message equals the original corpus text for the same fixture
  index; overlap statistics logged; a factually-correct paraphrase passes validators.
- Generation policy: bounded attempts; `generation_missing` recorded on bound exhaustion; no
  resampling beyond the bound.
- Corpus frozen once: re-freeze with changed content is refused; every supervisor schedule entry
  resolves its text hash from the frozen corpus.

### `tests/test_seal_guards.py`
- Account evidence: missing / tampered / stale snapshot → fail closed; config balance or prices ≠
  snapshot → refuse; missing `account_evidence_sha256` → refuse.
- Global ceiling: committed across stage ledgers + original 4.10737974 against
  `authorized_ceiling_credits` 1490 → stop before send with a recorded reason.
- `.run.lock`: second concurrent runner fails; clean exit releases; crash leaves the lock.
- No silent replay: pending episode in the ledger → `ResumeError`.
- Path guards: writes into `data/study/` refused; missing `--study-dir` refused.
- Source contract: runner refuses to start if any of the five pinned module hashes drifts.

After any change to the tree, `python scripts/reproduce.py --verify-only` must still pass (new
files are additive and do not touch the five pinned modules or `data/study/`).

## 11. Blockers and caveats

| ID | Item | Impact | Owner / action |
| --- | --- | --- | --- |
| B-01 | `scripts/mutate_gate.py` + `tests/test_gate_mutations.py` absent, though stage-2 outputs exist and are complete; the manifest pins `script_sha256 e6be64c5…0957` | Stage-2 acceptance ("deterministic reruns match; tests exercise block/abstain/publication") is unverifiable until restored | Offline child / parent: restore byte-identical to the pinned hash or regenerate with a documented amendment |
| B-02 | `scripts/run_live_extension.py` pinned in frozen `provenance/analysis_plan.json` but absent from the tree and all git history | Unrecoverable legacy runner gap; does not block offline reproduction (frozen data verified) | Record in the legacy manifest; no action possible |
| B-03 | `scripts/prepare_live_paper.py` (`e17a7530…`) and `paper/live_body.tex` (`eae29500…`) drifted from pinned hashes in `analysis_plan.json` / `reporting_history.json` | None for stages 4–6; permitted by the source contract ("reporting and packaging code may evolve independently") | Record in the legacy manifest |
| B-04 | Current balance, prices, and model availability unverifiable without credentials | Stages 4–6 cannot start until the parent's evidence snapshot exists and passes verification | Parent: produce `data/account_evidence/<date>/` (§4); runners fail closed meanwhile |
| B-05 | Frozen default paths dangle (`DEFAULT_STUDY_DIR` → `results/adaption_live`; `worker_output_root` → `results/…`) | A bare `python -m src.live_study …` run would fail or misplace artifacts | Handled by design: every new run passes `--study-dir` and v2 defines its own worker paths; path guard rejects the defaults |
| B-06 | Git "dubious ownership" on this checkout | Any git query needs `git -c safe.directory=G:/swarm-alignment-2026-sprint …` | Operational note |
| B-07 | ADMIT portal and workshop website were inaccessible during plan verification | Submission portal, deadline, length, and non-archival status remain user-supplied requirements | Parent verifies the CFP directly before the 16 November submission |

None of B-01–B-03 blocks stages 4–6 technically; B-04 blocks all collection start by design.

## 12. Schedule (all times IST, from the plan's venue delivery schedule)

| When | Work |
| --- | --- |
| **13 Nov (today)** | This preflight; parent produces the evidence snapshot (B-04); offline child finishes stages 1–3, the generated claim ledger, and the AGENTVERIFY draft; freeze numeric artifacts once offline checks pass. Stage 4 protocol freeze may proceed afterward (not on the archival submission's critical path). |
| **14 Nov, by 12:00** | Freeze AGENTVERIFY PDF; submit by 15:00 (supplied deadline 17:29). Stage 4 collection + freeze proceeds after its protocol/analysis spec is committed. |
| **14–15 Nov** | Stage 4 frozen, then stage 5, in that order. ADMIT prepared in parallel only through work that does not assume new results; ADMIT frozen 15 Nov evening; submit 16 Nov 15:00. |
| **16–18 Nov** | Stage 6, only after stages 4 and 5 are stored and frozen. Its results enter AAAI only if the full study freezes by 18 Nov evening; otherwise it continues for A³ (user checkpoint 29 Nov; no manuscript file created now). AAAI rebuild 19–20 Nov, PDF frozen 20 Nov evening, submit 21 Nov 15:00 (supplied deadline 17:29). |

Internal freeze times are buffers; the minute before a deadline is not working time.

## 13. Claims discipline (binding on all stage 4–6 wording)

Restated from the plan's required claims, as they constrain these collections:

- Attacks are instructed; the sink is fake and local; `side_effects` is false; signing keys are
  public demo keys. Report exact per-cell counts; receipts and state do not publish every case.
- Provenance execution zeros come from `_provenance_action`, not learned refusal; its equality
  check blocks when `trusted_summary != summary`, and earlier binding/authorization checks can also
  veto — including a corrected true summary on an illegitimate fixture.
- Saved provenance observations were shown the true summary when available; the blind extension
  must remain distinguishable (§9).
- All 280 accepted original worker calls copied the template because `wording_reference` was in
  the prompt — that is what stage 6 removes, and it is the only claim its removal supports.
- Paired contrasts retain 2/4 families per Gemma attack stratum, 0/4 Qwen single, 1/4 Qwen
  coordinated; `incident-timeline` is unpaired in every attack stratum.
- Never claim: learned refusal, general weakness of receipts, spontaneous collusion, or a causal
  coordination effect from three speakers. The stage-5 retry is a single feedback-conditioned
  supervisor retry, not an adaptive attacker study or evidence of training. Stage 6's honest
  one-speaker control narrows a limitation; attack speaker roles and message count still co-vary.
- Negative or null results are valid outcomes; missing data and failed gates remain visible.

## 14. Appendix: paths

**Existing, untouched:** `docs/seal-venue-execution-plan.md` (governing plan);
`data/study/` (immutable; `main/`, `pilot/`, `workers/`, `provenance/`, `reference/`,
`checksums.json`); `src/live_study.py`, `src/live_fixtures.py`, `src/live_workers.py`,
`src/live_analysis.py`, `src/integrity.py`; `scripts/reproduce.py`;
`build/gate_mutations.csv`, `build/gate_mutations.manifest.json`.

**New, proposed by this preflight:** `src/seal_stages.py`;
`scripts/run_blind_provenance.py`, `scripts/run_gate_retry.py`, `scripts/run_wording_v2.py`,
`scripts/verify_account_evidence.py`; `configs/seal/stage4_budget.json`, `stage5_budget.json`,
`stage6_workers_budget.json`, `stage6_supervisor_budget.json`, `stage6_dev_budget.json`;
`tests/test_seal_blind.py`, `tests/test_seal_retry.py`, `tests/test_seal_wording_v2.py`,
`tests/test_seal_guards.py`.

**Collection outputs (created per stage, frozen before the next stage):**
`data/study_blind_provenance/` (protocol.json, schedule.json, denominator.jsonl, ledger.json,
episodes/, responses.jsonl, run_manifest.json, checksums.json);
`data/study_gate_retry/` (protocol.json, eligibility_manifest.json, retry_links.jsonl,
denominator.jsonl, ledger.json, episodes/, responses.jsonl, checksums.json);
`data/study_wording_v2/` (protocol.json, corpus_v2.json, workers/development/, workers/test/,
denominator.jsonl, ledger.json, episodes/, responses.jsonl, controls_report.json, checksums.json);
`data/account_evidence/<date>/` (balance.json, prices.json, models.json, evidence_manifest.json);
`build/seal/analysis/provenance_comparison.csv`; `build/seal/budget_status.json` (shared
cross-stage accounting report, regenerated by every runner).
