> Historical implementation checkpoint. Current paths and rebuild rules are in [seal-deliverables.md](seal-deliverables.md); completion is determined by the frozen ledgers and rebuild manifests.

# SEAL blind provenance implementation (stage 4)

Status: software complete; no collection run performed. This document records the
implementation of `src/seal_collection.py` and `scripts/run_blind_provenance.py`.

## Files

- `src/seal_collection.py` — stage 4 collection software (blinding wrapper, schedule,
  freeze/reconcile, raw request/response logging, global ceiling guard, comparison).
- `scripts/run_blind_provenance.py` — CLI: `freeze-protocol`, `dry-run`, `collect`,
  `freeze-study`, `compare`, `smoke`.
- `tests/test_seal_blind.py`, `tests/test_seal_collection_guards.py` — offline tests.
- `configs/seal/stage4_budget.json` — pinned budget config (balance/prices/models
  from `data/account_evidence/2026-11-13`).

## Schedule

240 calls = 40 fixture indexes x 3 conditions (single, coordinated, honest) x 2
models (gemma-4-31b-it, qwen3.5-397b), provenance arm only. 160 attack + 80 honest.

## Blinding

`supervisor_observation` is wrapped process-locally so `provenance.trusted_summary`
is set to `None`; the fixture and `execute_decision` are unchanged. Raw request and
response are logged to `responses.jsonl` with fsync per record.

## Collection status

No real collection has been performed. `collect` with `client=None` requires
`ADAPTION_API_KEY`, the explicit authorization token, and a verified evidence
snapshot; all three are absent from this implementation step. Offline full-schedule
tests use an injected client and do not touch `data/study`.

## Verification

- `python scripts/reproduce.py --verify-only` unchanged.
- `python -m pytest tests/test_seal_blind.py tests/test_seal_collection_guards.py -q` passes.
PY