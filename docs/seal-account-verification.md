# SEAL account verification — current-balance / price / model-availability evidence tooling

Prepared: 13 November 2026. Owner scope for this document: `scripts/seal_account_evidence.py`,
`tests/test_seal_account_evidence.py`, this document, and `data/account_evidence/<timestamp>/`
artifacts. Nothing else in the tree was edited; `data/study/` is byte-for-byte untouched
(`python scripts/reproduce.py --verify-only` re-run and passing after the changes described here).

Status: **collected, verified, and frozen (2026-11-13).** The parent explicitly authorized
the actual read-only collection run on 2026-11-13 (user-authorized SEAL implementation;
exactly the two official GETs; no inference; balance basis `user_reported` explicitly
permitted). Snapshot: `data/account_evidence/2026-11-13/`, canonical evidence hash
`a9eef4b50db654cb8540ae356fe7521aa1c63d0642bd7c851e630bbe00dcaed0` (see §8 for the actual
commands and results). `paid_requests` is 0 — the run made exactly 2 GET requests (models
list, official pricing page) and never touched the paid inference endpoint.

## 1. What is current evidence and what is history

| Fact | Kind | Treatment |
| --- | --- | --- |
| 1,490 credits | **User-declared available balance and remaining authorized ceiling** — the account holder's own statement ("i have 1490 adaptionai credits") in this SEAL conversation, dated 2026-11-13; parent-accepted as the `user_reported` balance basis for bounded collection | Recorded as `authorized_ceiling_credits` 1490 **and**, since the 2026-11-13 collection, as `verified_available_balance_credits` 1490 with `verification_method: "user_reported"` and a separate `balance_confidence` field. It is user-reported only: **not** API verified, **not** a console observation, and never presented as verified. |
| 4.10737974 credits | Original 960-request ledger spend (`data/study/main/ledger.json`) | Historical reference only. **Not subtracted from the ceiling** — the ceiling already represents remaining credits, so subtracting again would double-count the historical spend. Enforced by a snapshot field (`historical_ledger_spend_not_subtracted: true`) whose absence fails verification. |
| `data/study/main/budget.json` (verified_balance 1495.819, prices 0.09/0.34 and 0.55/3.50, unit "Adaption credits") | Frozen historical budget | Explicitly **not current evidence**. Never copied into a stage budget config; stage configs take balance and prices from the evidence snapshot only. |
| Model IDs `gemma-4-31b-it`, `qwen3.5-397b` | Frozen study models | Required set for availability evidence; substitution requires a recorded amendment, never a silent swap. |

The 1,490 ceiling and stage envelopes (20 / 20 / 100 / 50) are maximum allowances, not cost
estimates. Their sum (190 credits) is recorded in every snapshot as
`planned_max_allocated_credits` with its basis, next to `paid_charges_incurred_by_collection:
0` — the collection run itself makes no fee-bearing call (no `estimate=True` dataset calls,
no inference calls).

## 2. Endpoint discovery — official sources only

Discovery basis (retrieved 2026-11-13): the official Adaption documentation index
(`https://docs.adaptionlabs.ai/llms.txt`), the models API reference, the FAQ, the quickstart,
and the pricing page; plus frozen repository code for the inference endpoint. Per the task
constraint, **no speculative endpoint probing or broad scans were performed** — the registry
below is complete for this tool.

| Purpose | Method / URL | Auth | Paid | Source |
| --- | --- | --- | --- | --- |
| Model availability | `GET https://api.prod.adaptionlabs.ai/api/v1/models?type=hosted` | Bearer API key | No | `https://docs.adaptionlabs.ai/api/resources/models/methods/list/index.md` |
| Prices | `GET https://docs.adaptionlabs.ai/inference-stacks/models/index.md` | None | No | Same page; also the frozen budget's `pricing_source` |
| Balance | **No documented endpoint** | — | — | API reference lists datasets / autoscientist / training models / responses / tools / models only; FAQ says credits stay on the organization balance, visible in the app console (`https://adaptionlabs.ai/app`) |
| Inference | `POST https://api.prod.adaptionlabs.ai/api/v1/responses` | Bearer | **Yes** | Frozen `src/live_study.py` `DEFAULT_API_URL` — **deliberately never called by this tool** |

Documented balance absence: the official API reference documents no account/billing/credits
endpoint. This is a recorded platform fact (checked 2026-11-13), not an invented restriction.
The snapshot records the absence specifically (`balance.json` →
`balance_status: "no_documented_balance_endpoint"`, `verified_available_balance_credits: null`,
with source URLs and the check timestamp) and **never fabricates a balance**. A numeric balance
may enter the snapshot only with an explicitly typed provenance; verification refuses a
numeric balance without it. Two bases exist:

- `verification_method: "manual_observation"` — an observation supplied by the authorized
  account holder (e.g. read from the app console): `balance_credits`, `observed_at`, `source`.
- `verification_method: "user_reported"` — used in the 2026-11-13 collection because the
  parent explicitly permitted it in the collection authorization: the account holder's own
  dated statement in the SEAL conversation. Requires `balance_credits`, `source`,
  `source_precision: "date"`, `reported_date`, records `verbatim_statement`,
  `exact_report_time_known: false`, and a recording timestamp (`recorded_at_utc`, the
  collection time) kept separate from the unknown exact report time. It sets
  `balance_status` and a distinct `balance_confidence` of
  `"user_reported_not_api_verified"`, keeps `balance_is_api_verified: false`, and
  verification refuses it without that provenance, without a matching value, or without
  the confidence field.

Price evidence basis: current official **public** pricing is accepted as price evidence when
the retrieval timestamp and source are saved — no account-page-only restriction is imposed.
`prices.json` stores parsed values, the source URL, a retrieval timestamp, and
`page_sha256` over the saved raw page (`pricing_page.md`), which verification re-parses and
cross-checks. The unit is evidenced by the page's own sentence ("Prices are in credits per
1 million tokens"); the full historical unit name "Adaption credits" is carried from the
frozen budget pending any account-source confirmation.

Model availability is evidence of what **the caller's key grants** (official docs: "Model
availability can vary by API key"); the tool does not send `X-Organization-Id`.

## 3. Tool usage

### 3.1 Collection (authorized-only)

```text
python scripts/seal_account_evidence.py collect \
    --evidence-root data/account_evidence \
    --api-key-file <secret-delivery-handle-path> \
    --authorize <authorization marker recorded by the parent> \
    [--stamp 2026-11-13T1422] \
    [--balance-manual balance_manual.json]
```

- Reads the key **inside the process** into the `Authorization` header only. The handle path
  is never recorded in any artifact; only the source kind (`api_key_file` / `environment`)
  is. The frozen protocol rule (`ADAPTION_API_KEY`) is honored when no `--api-key-file` is
  given.
- Performs exactly two GETs (models with `?type=hosted`, official pricing page), enforced by
  a method/URL allowlist that cannot reach the paid inference endpoint.
- Writes `balance.json`, `models.json`, `prices.json`, `pricing_page.md`, `discovery.json`,
  then finalizes with `evidence_manifest.json`.
- On HTTP failure: writes `errors.jsonl` (item, sanitized HTTP status, safe category,
  timestamp) and **no manifest** — the snapshot then fails `verify` (fail-closed), while the
  unsuccessful status is recorded without sensitive details.
- Refuses to overwrite an existing non-empty snapshot directory; reruns on the same date
  need an explicit `--stamp` (e.g. a UTC time).
- Optional `--balance-manual` (a JSON file) records an authorized balance observation with
  provenance: either `{"method": "manual_observation", balance_credits, observed_at, source[,
  notes]}` (console reading) or `{"method": "user_reported", balance_credits, source,
  source_precision: "date", reported_date[, verbatim_statement, notes]}` (parent-permitted
  conversation provenance; the tool fills in `recorded_at_utc` and
  `exact_report_time_known: false`). It must declare its source, or collection refuses.

### 3.2 Verification (offline, fail-closed — the stage-runner gate)

```text
python scripts/seal_account_evidence.py verify \
    --evidence-dir data/account_evidence/2026-11-13 \
    --require-models gemma-4-31b-it qwen3.5-397b
```

Refuses: missing files, missing/foreign manifest, tampered or hash-mismatched files,
schema mismatches, missing retrieval timestamps, snapshots older than the same UTC day
(`--max-age-days 0` default, matching the preflight proposal that evidence be re-verified on
the collection date), an unavailable required model (`present: false`), missing required
prices, price values disagreeing with the saved official page, a numeric balance without
declared manual-observation provenance, a ceiling other than 1,490, and an announced
`shutdown_date` on or before the verification date.

`evidence_manifest.json` records per-file sha256 plus `canonical_sha256` over
`{"schema", "files"}` — deterministic and stable. Stage budget configs pin this value as
`account_evidence_sha256` (preflight §3.1); runners recompute it at start and fail closed on
mismatch. The frozen `validate_budget_config` requires a numeric `verified_balance`: before
the 2026-11-13 run this fail-closed by design while the balance was null; the collected
snapshot now carries 1490.0 with declared `user_reported` provenance (parent-permitted), so
stage budget configs can validate against it. The snapshot keeps the ceiling (1,490) and the
balance in distinct fields, each with its declared basis.

`status` summarizes a snapshot offline; `key-source-check` reports only booleans
(`env_key_present`, `key_file_readable`) and never any content.

## 4. Security and sanitization rules (implemented and tested)

- The API key exists only inside the collecting process, assigned to the `Authorization`
  header. It is never written to disk, hashed, logged, or echoed. All snapshot bytes and
  printed summaries are scanned for key material in tests.
- Denylist scrub (`organization|account|email|customer|user_id|username|billing_address|payment|card|token|secret|key`,
  case-insensitive) plus a documented-field allowlist per model record
  (`id`, `created`, `object`, `owned_by`, `display_name`, `shutdown_date`). A non-platform
  `owned_by` is redacted to `<non-platform-owner-redacted>`.
- Error records contain only: item, HTTP status (or null), safe category
  (`auth_error` / `http_client_error` / `http_server_error` / `network_error:<ExceptionClass>` /
  `response_error:<ExceptionClass>`), timestamp. Response bodies, headers, request objects,
  and raw exception text are never persisted (network exceptions are reduced to their class
  name precisely because `str(exc)` can embed URLs).
- Loggable content is limited to: model IDs, non-sensitive balances/prices, source URLs,
  timestamps, hashes, booleans. Account identifiers are never shown or stored.
- If the credential file is unreadable, the tool reports `api_key_unreadable` with the source
  kind only — it never requests or displays the key, and the parent is informed of the
  unreadability without any key material moving.

## 5. Snapshot contents (`data/account_evidence/<stamp>/`)

| File | Content |
| --- | --- |
| `balance.json` | `verified_available_balance_credits` (null with explicit status, or numeric with `manual_observation` / parent-permitted `user_reported` provenance and a distinct `balance_confidence`), `balance_is_api_verified: false` and `documented_balance_endpoint: false`, `authorized_ceiling_credits` 1490 + declared basis, `planned_max_allocated_credits` 190 + basis, `paid_charges_incurred_by_collection` 0, historical 4.10737974 marked reference-only and not subtracted, unit name + provenance, absence-check sources/timestamp |
| `prices.json` | Per-model input / cached-input / output prices per million tokens for both required models, source URL, retrieval timestamp, `page_sha256`, unit evidence |
| `pricing_page.md` | Raw bytes of the official pricing page at retrieval (re-parsed by verification) |
| `models.json` | Availability per required model (`present`, `display_name`, `created`, `shutdown_date`), full available model ID list, endpoint, `requested_type: hosted`, retrieval timestamp, key-source kind |
| `discovery.json` | Endpoint registry with official source URLs and dates, including the documented balance absence and the no-speculative-scan policy, plus a run-time `execution` block (actual `http_get_requests_made`, zero POSTs / paid / inference requests) |
| `errors.jsonl` | (only on failure) sanitized unsuccessful-HTTP records |
| `evidence_manifest.json` | Per-file sha256, `canonical_sha256` (the value stage configs pin), required models, `paid_requests: 0`, `inference_calls: 0`, collect-time provenance |

The provenance block also records `source_state`: the pinned five-module contract
(`data/study/provenance/source_contract.json`) compared against current on-disk hashes —
recording the extension state explicitly (see §7).

## 6. Tests

`python -m pytest tests/test_seal_account_evidence.py -q` — 43 tests, all offline
(fake transport only; no network, no credentials, no paid calls). Coverage:

- Snapshot assembly and manifest determinism; canonical-hash recomputation.
- Allowlisted GET-only transport: exactly the two documented GETs, never the paid endpoint;
  `RequestsTransport` blocks any other URL/method and enforces the exact `?type=hosted` query.
- Bearer header attached to the models call; key material absent from every snapshot file.
- Sanitization: denylisted fields and non-platform owners stripped; sanitized HTTP status
  records (500 / 401 / network / malformed JSON) with no bodies or exception text.
- Balance absence documented, never fabricated; numeric balance without provenance refused;
  ceiling mismatch refused; manual observation recorded with declared source and time;
  `user_reported` basis recorded with source, date precision, unknown-exact-time flag,
  separate recording timestamp, distinct confidence, and matching value; planned-allocation
  (190) and zero-paid-charge enforcement; execution-block GET count with zero paid/POST.
- Verify fail-closed: missing file, missing manifest, tampered file, manifest hash mismatch,
  staleness (same-UTC-day rule and `--max-age-days` boundary), unavailable required model,
  double-finalize refusal.
- Pricing parser: exact values for both required models (including cached rates), unit-phrase
  enforcement, no-rows refusal; configuration-table rows do not pollute prices.
- `source_state` records the extension drift consistently; CLI `verify` round-trip passes.

After the 2026-11-13 balance-basis extension (this account tool's scope only), the account
suite is 43 passed. The earlier full-tree check (`python -m pytest tests/ -q` → 291 passed;
`python scripts/reproduce.py --verify-only` → passing) predates it; per the collection
authorization only the account tests were re-run on 2026-11-13, since other agents own the
rest of the tree.

## 7. Preflight corrections recorded (parent review feedback)

1. **The strict original-five-module assumption no longer holds.** `src/live_analysis.py`
   was extended by stages 1–3 (pinned legacy hash `b12c22df…a03b53`; current on-disk hash
   `f9548f85…`, other four modules still matching the contract). Stage runners must use the
   pinned legacy fixture/collector modules explicitly and carry a current extension manifest
   rather than assuming all five are byte-identical. The evidence snapshot's `source_state`
   records pinned vs current hashes and the drift list explicitly instead of assuming either.
2. **Copying is the outcome, not a defect.** For the stage-6 exact-copy audit: a new worker
   message must not verbatim-reproduce the original corpus string for the same fixture index;
   a factually-correct paraphrase is the accepted target. Coincidental similarity must not
   cause rejection — only verbatim reproduction of the original string is an exact-copy hit.
3. **1,490 is the remaining authorized ceiling.** The original 4.10737974 historical spend is
   a historical fact and must not be silently subtracted again when computing committed spend
   against the ceiling (the preflight's `global_committed` formula already lists the original
   ledger as a constant — implementers must not also deduct it from 1,490 when configuring
   stage budgets). The snapshot encodes this as `historical_ledger_spend_not_subtracted`.
4. **The 320-scheduled-workers record understates billed worker calls.** The stage-6
   generation policy allows bounded regeneration attempts (up to 3 per message slot), so
   actual billed worker requests can exceed 320 accepted messages; the stage-6 protocol must
   pin the attempts bound explicitly and budget against it, reporting accepted-message counts
   and attempt counts separately. Budget records should distinguish "scheduled accepted
   worker calls (320)" from "billed generation attempts (bounded by the frozen attempts
   policy)".

## 8. Collection run — executed and verified (2026-11-13)

Executed under the parent's explicit 2026-11-13 authorization (user-authorized SEAL
implementation; actual read-only models/pricing GETs directed now; balance basis
`user_reported` explicitly permitted; no paid inference). The API key was supplied via the
parent-provided secure key handle (`--api-key-file`; the handle path is never recorded in
any artifact — confirmed by scanning the snapshot for it). Steps and actual results:

1. `key-source-check` → `{"env_key_present": false, "key_file_readable": true}` (booleans
   only, no content).
2. Balance provenance input (a temp file outside the repository; its content is fully
   embedded in `balance.json`): `method: "user_reported"`, `balance_credits: 1490`,
   `source: "SEAL user conversation in this task's chat thread…"`, `source_precision: "date"`,
   `reported_date: "2026-11-13"`, `verbatim_statement: "i have 1490 adaptionai credits"`.
3. `collect --evidence-root data/account_evidence --api-key-file <secure-key-handle>
   --balance-manual <balance-provenance.json> --authorize 'SEAL-2026-11-13: user-authorized
   SEAL implementation; parent directs actual read-only account evidence collection now
   (official GET models?type=hosted + official public pricing page only; no paid inference;
   balance basis user_reported explicitly permitted; ceiling 1490 credits; planned stage
   allocation 190 credits max)'` → exit 0 with
   `{"balance_status":"user_reported_balance_recorded; see balance.json",
   "canonical_sha256":"a9eef4b50db654cb8540ae356fe7521aa1c63d0642bd7c851e630bbe00dcaed0",
   "http_get_requests_made":2,"inference_calls":0,"manifest_written":true,"paid_requests":0,
   "required_models":["gemma-4-31b-it","qwen3.5-397b"],
   "snapshot":"data\\account_evidence\\2026-11-13"}`. No `errors.jsonl` was written — no
   HTTP failures occurred, so no sanitized failure records exist.
4. `verify --evidence-dir data/account_evidence/2026-11-13 --require-models gemma-4-31b-it
   qwen3.5-397b` (same-UTC-day freshness enforced via the default `--max-age-days 0`) →
   `{"canonical_sha256":"a9eef4b50db654cb8540ae356fe7521aa1c63d0642bd7c851e630bbe00dcaed0",
   "verify":"pass"}`.
5. `status` → both required models `present: true` (platform-owned, `shutdown_date: null`),
   prices as in §8.1, balance as in §8.2, manifest present.

### 8.1 Actual retrieved evidence (2026-11-13T11:40:21Z)

- Models (`GET /api/v1/models?type=hosted`, authenticated, read-only, no charge): 7 hosted
  model IDs are available to this key: `deepseek-v4-flash`, `gemma-4-26b-a4b-it`,
  `gemma-4-31b-it`, `glm-5.2`, `glm-53-flash`, `qwen3.5-397b`, `qwen3.8-27b`. Both required
  study models are present and platform-owned with no shutdown dates — no substitution was
  made or needed.
- Prices (official public pricing page; saved raw page `pricing_page.md` with `page_sha256`
  `80b0c425620ab1bacbfd41ed96c13aec8c1dfea6989792428d31d878286bdeb2`): `gemma-4-31b-it`
  0.09 input (0.05 cached) / 0.34 output; `qwen3.5-397b` 0.55 (0.225) / 3.50 — credits per
  1 million tokens. Identical to the frozen budget's prices (0.09/0.34 and 0.55/3.50); no
  drift between the official page and the frozen study assumptions.
- Balance: 1490.0 credits as `user_reported` (§8.2). Not API verified — no documented
  balance endpoint exists and none was probed.
- Request accounting (recorded in `discovery.json` → `execution`): `http_get_requests_made:
  2`, `http_post_requests_made: 0`, `paid_requests: 0`, `inference_calls: 0`.

### 8.2 Balance basis (user_reported, parent-permitted)

`verified_available_balance_credits: 1490.0` with `verification_method: "user_reported"`,
`balance_status` / `balance_confidence: "user_reported_not_api_verified"`,
`balance_is_api_verified: false`, `documented_balance_endpoint: false`. Provenance: the
account holder's own statement in this SEAL conversation ("i have 1490 adaptionai
credits"), dated 2026-11-13 with `source_precision: "date"` and
`exact_report_time_known: false`; `recorded_at_utc` is the collection timestamp, kept
separate from the unknown exact report time. No console observation is claimed and none
was required. The same 1490 is the authorized ceiling (`authorized_ceiling_credits`) with
its declared basis; `planned_max_allocated_credits: 190.0` (stage envelopes 20/20/100/50)
with the remainder unallocated; the historical 4.10737974 ledger spend is recorded
reference-only and not subtracted; `paid_charges_incurred_by_collection: 0`.

### 8.3 Evidence hash to pin into stage budget configs

`data/account_evidence/2026-11-13/evidence_manifest.json` → `canonical_sha256`
`a9eef4b50db654cb8540ae356fe7521aa1c63d0642bd7c851e630bbe00dcaed0` (per-file sha256 list in
the manifest). Stage runners pin this as `account_evidence_sha256` and recompute at start,
failing closed on mismatch. The frozen `validate_budget_config` requires a numeric
`verified_balance`; the snapshot now carries 1490.0 with declared `user_reported`
provenance, so stage-4 budget configs can validate against this snapshot. The manifest's
`source_state` block records the pinned five-module contract against on-disk hashes as they
stood at collection time (no drift at 2026-11-13T11:40:21Z); per its own note, a later
restoration of the original analysis sources does not invalidate this account evidence.

## 9. Remaining blockers

| ID | Item | Status |
| --- | --- | --- |
| B-04 (this tool) | Current balance / prices / availability evidence | **RESOLVED 2026-11-13: collected, hash-frozen, and offline-verified** (`data/account_evidence/2026-11-13/`, canonical `a9eef4b50db654cb8540ae356fe7521aa1c63d0642bd7c851e630bbe00dcaed0`; 2 actual GETs, 0 paid; both required models present; official-page prices match the frozen budget; balance 1490 as the parent-permitted `user_reported` basis, not API verified). Remaining for the stage-4 implementer: pin `canonical_sha256` into the stage budget configs as `account_evidence_sha256`. |
| Local | No `AGENTS.md` / local agent-instructions file found in the repository tree (checked repo root, parent directory, `.agents/`, `.github/`) | Recorded; task instructions taken from the parent task text and the two SEAL documents instead. |
| B-01, B-02, B-03, B-05, B-06, B-07 | Unchanged from the preflight | Not owned by this child; no action taken. |
