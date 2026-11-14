"""SEAL current-account evidence collection and verification (stages 4-6 prerequisite).

Blocker B-04 prerequisite tooling for docs/seal-venue-execution-plan.md. Produces a
sanitized, hash-pinned evidence snapshot under data/account_evidence/<stamp>/ that
stage budget configs pin via their canonical sha256 (account_evidence_sha256).

What this tool does (read-only account discovery; NO inference, NO paid calls):
  1. GET  https://api.prod.adaptionlabs.ai/api/v1/models?type=hosted
     Documented official endpoint (Adaption API reference, models/list). Read-only,
     no charge. Evidences current model availability for the caller's key.
  2. GET  https://docs.adaptionlabs.ai/inference-stacks/models/index.md
     Official public pricing reference (the frozen budget's pricing_source). No
     auth. Current public prices are valid price evidence when the retrieval
     timestamp and source are saved (parent instruction: no account-page-only
     restriction is imposed).
  3. Balance: the official Adaption API reference documents NO account/billing/
     credits-balance endpoint (checked 2026-11-13). The snapshot records that
     absence specifically and never fabricates a balance. A balance number may be
     recorded only with an explicitly typed provenance: a manual observation by
     the authorized account holder (e.g. read from the app console), or — when
     the parent explicitly permits it in the collection authorization — a
     user_reported statement sourced to the SEAL conversation itself (date-only
     precision; never API verified, confidence recorded separately).
  4. POST /api/v1/responses (paid inference) is NEVER called by this tool.
     paid_requests is 0 by construction and enforced by a URL/method allowlist.

Security invariants:
  - The API key is read from ADAPTION_API_KEY (frozen protocol rule,
    data/study/main/protocol.json budget_rules.credentials_env) or from
    --api-key-file (a secret-delivery handle path). The key exists only inside
    this process, is assigned to the Authorization header, and is never written,
    logged, hashed, or echoed. The handle path itself is never recorded; only the
    source kind ("api_key_file" / "environment") is recorded.
  - Errors are recorded as HTTP status + safe category only. Response bodies,
    request objects, headers, and raw exception text are never persisted.
  - Snapshot records contain model IDs, non-sensitive balances/prices, source
    URLs, timestamps, and hashes. Personal/account fields are stripped from API
    responses (denylist scrub + per-item field allowlist).

Commands:
  collect   Authorized-only snapshot collection (requires --authorize).
  verify    Offline schema/hash/staleness validation; writes evidence_manifest.json
            when absent, fails closed on tamper/staleness/missing evidence.
  status    Offline summary of a snapshot directory.
  key-source-check  Reports (booleans only) whether a credential source is usable.

The collection run itself is gated: the parent authorizes collection after an
offline independent review. Nothing in this file collects until then.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = ROOT / "data" / "account_evidence"

SCHEMA_NAME = "seal-account-evidence-v1"
SNAPSHOT_FILES = ("balance.json", "discovery.json", "models.json", "prices.json", "pricing_page.md")

API_BASE = "https://api.prod.adaptionlabs.ai"
MODELS_URL = f"{API_BASE}/api/v1/models"
RESPONSES_URL = f"{API_BASE}/api/v1/responses"
PRICING_URL = "https://docs.adaptionlabs.ai/inference-stacks/models/index.md"

MODELS_LIST_DOC = "https://docs.adaptionlabs.ai/api/resources/models/methods/list/index.md"
API_REFERENCE_INDEX = "https://docs.adaptionlabs.ai/llms.txt"
PRICING_DOC = "https://docs.adaptionlabs.ai/inference-stacks/models/index.md"
FAQ_DOC = "https://docs.adaptionlabs.ai/resources/faq/index.md"

# Frozen-code source for the inference endpoint (never called here):
# src/live_study.py DEFAULT_API_URL.
FROZEN_RESPONSES_SOURCE = "src/live_study.py DEFAULT_API_URL"

REQUIRED_MODELS = ("gemma-4-31b-it", "qwen3.5-397b")

# User-declared remaining authorized ceiling (parent authorization, 2026-11-13).
# This is a spending ceiling, NOT a verified available balance. The historical
# 960-request ledger spend below is a recorded historical fact and is NOT
# subtracted from this ceiling (it already represents remaining credits).
AUTHORIZED_CEILING_CREDITS = 1490.0
AUTHORIZED_CEILING_BASIS = (
    "User-declared available balance and remaining authorized ceiling (user statement "
    "'i have 1490 adaptionai credits' in this SEAL conversation, dated 2026-11-13; "
    "parent-accepted for bounded collection). User-reported only: not an API-verified "
    "balance and not a console observation; treated as a spending ceiling, not a "
    "verified available balance."
)
PLANNED_MAX_ALLOCATED_CREDITS = 190.0
PLANNED_MAX_ALLOCATED_BASIS = (
    "Sum of the parent-approved stage envelopes in docs/seal-venue-execution-plan.md "
    "(20 blind provenance + 20 eligible retries + 100 wording/control study + 50 bounded "
    "development and collection failures). Maximum allowances, not cost estimates; the "
    "remainder stays unallocated."
)
USER_REPORTED_BALANCE_PERMISSION = (
    "Balance basis 'user_reported' is used here only because the parent explicitly "
    "authorized it in the 2026-11-13 collection authorization. No documented balance "
    "endpoint exists; the value is never API verified and its confidence is recorded "
    "separately as user_reported_not_api_verified."
)
HISTORICAL_LEDGER_SPENT_CREDITS = 4.10737974
HISTORICAL_LEDGER_NOTE = (
    "Original 960-request ledger spend (data/study/main/ledger.json). Historical fact "
    "recorded for reference only; NOT subtracted from the authorized ceiling."
)
UNIT_NAME = "Adaption credits"

# Official-docs discovery dates for the endpoint registry below.
DISCOVERY_DATE = "2026-11-13"

USER_REPORTED_DETAIL = (
    "The official Adaption API reference documents no account, billing, or credits-balance "
    "endpoint (checked " + DISCOVERY_DATE + "); the balance is NOT API verified and no console "
    "observation is claimed. The value is the account holder's own statement in this SEAL "
    "conversation, dated " + DISCOVERY_DATE + " (exact report time unknown), accepted by the "
    "parent as the user-declared available balance and remaining authorized ceiling for "
    "bounded collection. It must never be presented as verified."
)

# Base URLs this tool may request, and the only methods it may use for them.
# Nothing else is reachable; POST /responses is deliberately absent. The models
# request must carry exactly the documented query string.
TRANSPORT_ALLOWLIST: dict[str, tuple[str, ...]] = {
    MODELS_URL: ("GET",),
    PRICING_URL: ("GET",),
}
MODELS_REQUIRED_QUERY = "?type=hosted"

HTTP_TIMEOUT_SECONDS = 30

DENYLIST_KEY_RE = re.compile(
    r"organiz|account|email|customer|user_id|username|billing_address|payment|card|token|secret|key",
    re.IGNORECASE,
)
# "key"/"token" in the denylist would strip ordinary payload keys like
# "shutdown_date"? No: the regex matches key *names*; "shutdown_date" contains
# neither. But "display_name" contains no match either. Model "id" is fine. Note
# that a field literally named "api_key" or "access_token" is stripped.

MODEL_FIELD_ALLOWLIST = ("id", "created", "object", "owned_by", "display_name", "shutdown_date")
GENERIC_OWNER_VALUES = frozenset({"platform", "adaption", "system"})

PRICING_ROW_RE = re.compile(
    r"\|\s*\[`(?P<model>[^`|]+)`\]\([^)]*\)\s*\|"
    r"\s*(?P<input>\d+(?:\.\d+)?)(?:\s*\((?P<cached>\d+(?:\.\d+)?)\))?\s*\|"
    r"\s*(?P<output>\d+(?:\.\d+)?)\s*\|"
)
PRICING_UNIT_PHRASE = "credits per 1 million tokens"


class EvidenceError(RuntimeError):
    """Base error; messages are safe to display (no credentials, no raw bodies)."""


class SnapshotError(EvidenceError):
    """Snapshot validation failure (fail-closed)."""


class TransportBlocked(EvidenceError):
    """A request outside the method/URL allowlist was attempted."""


class HttpFailure(EvidenceError):
    """A sanitized HTTP failure record (status + category only)."""

    def __init__(self, item: str, http_status: int | None, category: str) -> None:
        self.item = item
        self.http_status = http_status
        self.category = category
        super().__init__(f"{item}: {category}" + (f" (http_status={http_status})" if http_status else ""))


def utcnow() -> datetime:
    """Module-level so tests can pin the clock."""
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime | None = None) -> str:
    return (moment or utcnow()).isoformat()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return content_sha256(path.read_bytes())


def parse_iso(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError("missing ISO-8601 UTC timestamp")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"unparseable ISO-8601 timestamp: {value!r}") from exc
    if moment.tzinfo is None:
        raise SnapshotError("timestamp must carry a UTC offset")
    return moment.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Credential handling (content never leaves the process)
# ---------------------------------------------------------------------------


def read_api_key(*, api_key_file: str | None) -> tuple[str | None, str]:
    """Return (key_or_None, source_kind). The key value is never logged."""
    if api_key_file:
        try:
            raw = Path(api_key_file).read_text(encoding="utf-8")
        except OSError:
            return None, "api_key_file"
        key = raw.strip()
        return (key or None), "api_key_file"
    env_key = os.environ.get("ADAPTION_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip(), "environment"
    return None, "none"


def key_source_status(api_key_file: str | None) -> dict[str, Any]:
    """Boolean-only report for the parent (no content, no lengths)."""
    env_present = bool((os.environ.get("ADAPTION_API_KEY") or "").strip())
    if api_key_file:
        try:
            with Path(api_key_file).open("rb") as handle:
                readable = len(handle.read(1)) > 0
        except OSError:
            readable = False
    else:
        readable = False
    return {"env_key_present": env_present, "key_file_readable": readable}


# ---------------------------------------------------------------------------
# HTTP transport: allowlisted, GET-only, sanitized errors
# ---------------------------------------------------------------------------


class TransportResult:
    __slots__ = ("http_status", "text")

    def __init__(self, http_status: int, text: str | None) -> None:
        self.http_status = http_status
        self.text = text


class HttpTransport:
    """GET-only transport restricted to TRANSPORT_ALLOWLIST URLs."""

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> TransportResult:
        raise NotImplementedError


class RequestsTransport(HttpTransport):
    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self.calls: list[tuple[str, str]] = []

    def _guard(self, url: str, method: str) -> None:
        base = url.split("?", 1)[0]
        allowed_methods = TRANSPORT_ALLOWLIST.get(base)
        if allowed_methods is None or method not in allowed_methods:
            raise TransportBlocked(f"blocked non-allowlisted request: {method} (host masked)")
        if base == MODELS_URL and not url.endswith(MODELS_REQUIRED_QUERY):
            raise TransportBlocked("models request must use exactly " + MODELS_REQUIRED_QUERY)

    def get(self, url: str, *, headers: Mapping[str, str] | None = None) -> TransportResult:
        self._guard(url, "GET")
        self.calls.append(("GET", url))
        try:
            response = self.session.get(url, headers=dict(headers or {}), timeout=HTTP_TIMEOUT_SECONDS)
        except requests.RequestException:
            # Sanitized: exception class only; never str(exc) (may embed URLs/proxy data).
            raise HttpFailure(url, None, f"network_error:{type(exc).__name__}") from None
        try:
            text = response.text
        except Exception:
            text = None
        return TransportResult(response.status_code, text)


def check_response(item: str, result: TransportResult) -> TransportResult:
    status = result.http_status
    if 200 <= status < 300:
        return result
    if status in (401, 403):
        category = "auth_error"
    elif 400 <= status < 500:
        category = "http_client_error"
    elif status >= 500:
        category = "http_server_error"
    else:
        category = "http_unexpected_status"
    raise HttpFailure(item, status, category)


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


def scrub_denylisted_keys(value: Any) -> Any:
    """Recursively drop dict keys whose names look account/personal/credential-like."""
    if isinstance(value, dict):
        return {
            key: scrub_denylisted_keys(item)
            for key, item in value.items()
            if not (isinstance(key, str) and DENYLIST_KEY_RE.search(key))
        }
    if isinstance(value, list):
        return [scrub_denylisted_keys(item) for item in value]
    return value


def sanitize_model_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Allowlist documented model fields; redact non-generic owners."""
    cleaned = scrub_denylisted_keys(dict(raw))
    record: dict[str, Any] = {}
    for field in MODEL_FIELD_ALLOWLIST:
        if field not in cleaned:
            continue
        value = cleaned[field]
        if field == "owned_by":
            value = value if value in GENERIC_OWNER_VALUES else "<non-platform-owner-redacted>"
        record[field] = value
    if "id" not in record or not isinstance(record["id"], str):
        raise EvidenceError("models response item lacks a string id (possible schema change)")
    return record


def sanitize_error_record(item: str, failure: HttpFailure, moment: datetime) -> dict[str, Any]:
    """HTTP status + safe category only. No bodies, no URLs-with-secrets, no headers."""
    return {
        "item": item,
        "http_status": failure.http_status,
        "category": failure.category,
        "retrieved_at_utc": iso_utc(moment),
    }


# ---------------------------------------------------------------------------
# Pricing page parsing (official public reference)
# ---------------------------------------------------------------------------


def parse_pricing_page(text: str) -> dict[str, dict[str, float]]:
    parsed: dict[str, dict[str, float]] = {}
    for match in PRICING_ROW_RE.finditer(text):
        model = match.group("model").strip()
        if not model or model.lower() == "model id":
            continue
        try:
            input_price = float(match.group("input"))
            output_price = float(match.group("output"))
            cached = float(match.group("cached")) if match.group("cached") else None
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) and v >= 0 for v in (input_price, output_price)):
            continue
        if cached is not None and not (math.isfinite(cached) and cached >= 0):
            cached = None
        parsed[model] = {
            "input_price_per_million": input_price,
            "cached_input_price_per_million": cached,
            "output_price_per_million": output_price,
        }
    if not parsed:
        raise EvidenceError("official pricing page parsed to no rows (page format may have changed)")
    if PRICING_UNIT_PHRASE not in text:
        raise EvidenceError("official pricing page no longer states its credit unit phrase")
    return parsed


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------


def pinned_source_contract() -> dict[str, Any]:
    """Pinned legacy five-module contract vs current on-disk hashes.

    The strict original-five-module assumption no longer holds: stages 1-3
    extended src/live_analysis.py, so its current hash may drift from the pinned
    legacy contract while the other four modules remain pinned. The snapshot
    records both explicitly instead of assuming either.
    """
    contract_path = ROOT / "data" / "study" / "provenance" / "source_contract.json"
    pinned: dict[str, str] = {}
    if contract_path.is_file():
        try:
            pinned = dict(json.loads(contract_path.read_text(encoding="utf8")).get("sha256") or {})
        except (json.JSONDecodeError, OSError):
            pinned = {}
    current: dict[str, str | None] = {path: sha256_file(ROOT / path) for path in pinned}
    drift = sorted(path for path, digest in current.items() if digest is not None and digest != pinned.get(path))
    missing = sorted(path for path, digest in current.items() if digest is None)
    return {
        "pinned_source_contract_path": "data/study/provenance/source_contract.json",
        "pinned_sha256": pinned,
        "current_sha256": current,
        "drift": drift,
        "missing": missing,
        "note": (
            "src/live_analysis.py was extended by stages 1-3; the strict "
            "original-five-module assumption no longer holds. Stage runners must "
            "use the pinned legacy fixture/collector modules explicitly and carry "
            "a current extension manifest; this block records the state as-is."
        ),
    }


def build_discovery_document(moment: datetime, http_get_requests_made: int | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": SCHEMA_NAME,
        "endpoint_scan_policy": (
            "Only endpoints documented in official Adaption docs (docs.adaptionlabs.ai) "
            "or already used by frozen repository code were registered. No speculative "
            "endpoint probing or broad scans were performed."
        ),
        "discovered_at_utc": iso_utc(moment),
        "endpoints": [
            {
                "purpose": "model_availability",
                "method": "GET",
                "url": MODELS_URL,
                "query": {"type": "hosted"},
                "auth": "required (Bearer ADAPTION_API_KEY)",
                "paid": False,
                "status": "used_by_this_tool",
                "source_urls": [MODELS_LIST_DOC, API_REFERENCE_INDEX],
                "source_dates": [DISCOVERY_DATE],
                "response_shape": 'object "list" with data[] items {id, created, object, owned_by, display_name?, shutdown_date?}',
            },
            {
                "purpose": "pricing_public_reference",
                "method": "GET",
                "url": PRICING_URL,
                "auth": "none",
                "paid": False,
                "status": "used_by_this_tool",
                "source_urls": [PRICING_DOC, "data/study/main/budget.json pricing_source"],
                "source_dates": [DISCOVERY_DATE],
            },
            {
                "purpose": "balance",
                "method": None,
                "url": None,
                "paid": False,
                "status": "no_documented_endpoint",
                "detail": (
                    "The official Adaption API reference documents no account, billing, "
                    "or credits-balance resource (datasets/responses/models/tools only). "
                    "Credits balance is visible in the app console "
                    "(https://adaptionlabs.ai/app). No speculative endpoint was probed."
                ),
                "source_urls": [API_REFERENCE_INDEX, FAQ_DOC],
                "source_dates": [DISCOVERY_DATE],
            },
            {
                "purpose": "inference_responses",
                "method": "POST",
                "url": RESPONSES_URL,
                "paid": True,
                "status": "excluded_paid_inference_not_called_by_this_tool",
                "source": FROZEN_RESPONSES_SOURCE,
            },
        ],
    }
    if http_get_requests_made is not None:
        document["execution"] = {
            "collected_at_utc": iso_utc(moment),
            "http_get_requests_made": int(http_get_requests_made),
            "http_post_requests_made": 0,
            "paid_requests": 0,
            "inference_calls": 0,
            "note": (
                "Run-time counts recorded by the collector: GETs actually issued against the "
                "transport allowlist (models + pricing). POST and paid counts are 0 because "
                "the paid inference endpoint is unreachable by the allowlist."
            ),
        }
    return document


MANUAL_OBSERVATION_FIELDS = ("balance_credits", "observed_at", "source", "notes")
USER_REPORTED_FIELDS = (
    "balance_credits",
    "source",
    "source_precision",
    "reported_date",
    "recorded_at_utc",
    "exact_report_time_known",
    "verbatim_statement",
    "notes",
)


def _checked_balance_number(observation: Mapping[str, Any]) -> float:
    balance = observation.get("balance_credits")
    if (
        not isinstance(balance, (int, float))
        or isinstance(balance, bool)
        or not math.isfinite(float(balance))
        or float(balance) < 0
    ):
        raise EvidenceError("balance observation must carry a finite non-negative balance_credits number")
    return float(balance)


def build_balance_document(moment: datetime, manual: Mapping[str, Any] | None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": SCHEMA_NAME,
        "verified_available_balance_credits": None,
        "balance_status": "no_documented_balance_endpoint",
        "balance_status_detail": (
            "The official Adaption API reference documents no account, billing, or "
            "credits-balance endpoint (checked " + DISCOVERY_DATE + "). The balance is not "
            "fabricated: it is null here until an explicitly sourced observation exists."
        ),
        "balance_is_api_verified": False,
        "documented_balance_endpoint": False,
        "authorized_ceiling_credits": AUTHORIZED_CEILING_CREDITS,
        "authorized_ceiling_basis": AUTHORIZED_CEILING_BASIS,
        "planned_max_allocated_credits": PLANNED_MAX_ALLOCATED_CREDITS,
        "planned_max_allocated_basis": PLANNED_MAX_ALLOCATED_BASIS,
        "paid_charges_incurred_by_collection": 0,
        "historical_ledger_spent_credits_reference_only": HISTORICAL_LEDGER_SPENT_CREDITS,
        "historical_ledger_spend_not_subtracted": True,
        "historical_ledger_note": HISTORICAL_LEDGER_NOTE,
        "unit_name": UNIT_NAME,
        "unit_name_provenance": (
            "Name carried from the frozen historical budget (data/study/main/budget.json); "
            "the official pricing page's 'credits per 1 million tokens' phrase confirms the "
            "credit unit (see prices.json unit_evidence). Full re-verification of the unit "
            "name against an account source remains pending."
        ),
        "retrieved_at_utc": iso_utc(moment),
        "absence_check": {
            "sources": [API_REFERENCE_INDEX, FAQ_DOC],
            "checked_at_utc": iso_utc(moment),
            "method": "official docs review only; no speculative endpoint probing",
        },
    }
    if manual is not None:
        method = manual.get("method") or ("manual_observation" if "observed_at" in manual else None)
        if method == "manual_observation":
            observation = {key: manual[key] for key in MANUAL_OBSERVATION_FIELDS if key in manual}
            if not isinstance(observation.get("source"), str) or not observation["source"].strip():
                raise EvidenceError("manual balance observation must declare its source")
            parse_iso(observation.get("observed_at"))
            document["verified_available_balance_credits"] = _checked_balance_number(observation)
            document["verification_method"] = "manual_observation"
            document["manual_observation"] = observation
        elif method == "user_reported":
            observation = {key: manual[key] for key in USER_REPORTED_FIELDS if key in manual}
            if not isinstance(observation.get("source"), str) or not observation["source"].strip():
                raise EvidenceError(
                    "user-reported balance must declare its source (the conversation it came from)"
                )
            if observation.get("source_precision") != "date":
                raise EvidenceError(
                    "user-reported balance must record source_precision 'date' (exact report time unknown)"
                )
            reported_date = observation.get("reported_date")
            if not isinstance(reported_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reported_date):
                raise EvidenceError(
                    "user-reported balance must record reported_date as a YYYY-MM-DD calendar date"
                )
            try:
                datetime.fromisoformat(reported_date)
            except ValueError as exc:
                raise EvidenceError(f"reported_date is not a valid calendar date: {reported_date!r}") from exc
            # Date-only provenance: the recording timestamp is this collection run, never
            # the (unknown) exact report time.
            observation.setdefault("exact_report_time_known", False)
            observation.setdefault("recorded_at_utc", iso_utc(moment))
            document["verified_available_balance_credits"] = _checked_balance_number(observation)
            document["balance_status"] = "user_reported_not_api_verified"
            document["balance_status_detail"] = USER_REPORTED_DETAIL
            document["verification_method"] = "user_reported"
            document["balance_confidence"] = "user_reported_not_api_verified"
            document["user_reported"] = observation
            document["user_reported_permission"] = USER_REPORTED_BALANCE_PERMISSION
        else:
            raise EvidenceError(
                "balance input must declare method 'manual_observation' (with observed_at) "
                "or 'user_reported' (parent-permitted date-precision conversation provenance)"
            )
    return document


def build_prices_document(
    parsed: Mapping[str, Mapping[str, float]],
    page_bytes: bytes,
    moment: datetime,
) -> dict[str, Any]:
    required_prices = {}
    for model in REQUIRED_MODELS:
        rates = parsed.get(model)
        if rates is None:
            required_prices[model] = None
            continue
        required_prices[model] = dict(rates)
    return {
        "schema": SCHEMA_NAME,
        "pricing_source": PRICING_URL,
        "pricing_source_kind": "official_public_documentation",
        "pricing_source_note": (
            "Current official public pricing is accepted as price evidence when the "
            "retrieval timestamp and source are saved (parent instruction). No "
            "account-page-only restriction is imposed."
        ),
        "retrieved_at_utc": iso_utc(moment),
        "page_sha256": content_sha256(page_bytes),
        "prices_per_million_tokens": required_prices,
        "unit_evidence": {
            "unit_token": "credits",
            "basis": f"official pricing page (retrieved {iso_utc(moment)}) states: '{PRICING_UNIT_PHRASE}'",
        },
        "required_models": list(REQUIRED_MODELS),
    }


def build_models_document(
    records: Sequence[Mapping[str, Any]],
    required_models: Sequence[str],
    moment: datetime,
) -> dict[str, Any]:
    by_id = {record.get("id"): record for record in records if isinstance(record.get("id"), str)}
    availability: dict[str, Any] = {}
    for model in required_models:
        record = by_id.get(model)
        if record is None:
            availability[model] = {"present": False}
        else:
            availability[model] = {
                "present": True,
                "display_name": record.get("display_name"),
                "created": record.get("created"),
                "object": record.get("object"),
                "owned_by": record.get("owned_by"),
                "shutdown_date": record.get("shutdown_date"),
            }
    return {
        "schema": SCHEMA_NAME,
        "models_endpoint": MODELS_URL,
        "requested_type": "hosted",
        "auth_note": "GET with Bearer key from ADAPTION_API_KEY or --api-key-file; X-Organization-Id not sent",
        "retrieved_at_utc": iso_utc(moment),
        "required_models": list(required_models),
        "availability": availability,
        "available_model_ids": sorted(by_id),
        "response_item_count": len(records),
        "sanitization": "denylist key scrub + documented-field allowlist; non-platform owners redacted",
    }


def finalize_snapshot(
    snapshot_dir: Path,
    required_models: Sequence[str],
    *,
    as_of: datetime | None = None,
    max_age_days: int = 0,
    write_manifest: bool = True,
    require_available: bool = True,
) -> dict[str, Any]:
    """Offline validation + manifest. Fails closed (SnapshotError) on any problem.

    require_available=False (collection time) records an unavailable required
    model honestly in the snapshot; require_available=True (verification, the
    stage-runner gate) refuses such a snapshot.
    """
    as_of = as_of or utcnow()
    missing = [name for name in SNAPSHOT_FILES if not (snapshot_dir / name).is_file()]
    if missing:
        raise SnapshotError(f"snapshot is missing required files: {missing}")
    if write_manifest and (snapshot_dir / "evidence_manifest.json").is_file():
        raise SnapshotError("evidence_manifest.json already exists; snapshots are frozen once finalized")

    documents: dict[str, Any] = {}
    for name in SNAPSHOT_FILES:
        if name.endswith(".md"):
            continue  # pricing_page.md is the raw official page bytes; validated via prices.json
        try:
            parsed = json.loads((snapshot_dir / name).read_text(encoding="utf8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SnapshotError(f"{name} is not valid JSON: {type(exc).__name__}") from exc
        if not isinstance(parsed, dict):
            raise SnapshotError(f"{name} must be a JSON object")
        documents[name] = parsed

    timestamps: list[datetime] = []
    for name in ("balance.json", "models.json", "prices.json", "discovery.json"):
        for key in ("retrieved_at_utc", "discovered_at_utc"):
            value = documents[name].get(key)
            if value:
                timestamps.append(parse_iso(value))
    if not timestamps:
        raise SnapshotError("snapshot carries no retrieval timestamps")

    # Balance: null is acceptable ONLY with an explicit status; a number requires provenance.
    balance_doc = documents["balance.json"]
    if balance_doc.get("schema") != SCHEMA_NAME:
        raise SnapshotError("balance.json schema mismatch")
    balance_value = balance_doc.get("verified_available_balance_credits")
    if balance_value is None:
        if not isinstance(balance_doc.get("balance_status"), str) or not balance_doc["balance_status"].strip():
            raise SnapshotError("null balance requires an explicit balance_status")
        if not isinstance(balance_doc.get("authorized_ceiling_basis"), str) or not balance_doc["authorized_ceiling_basis"].strip():
            raise SnapshotError("authorized ceiling must carry its declared basis")
    else:
        if not isinstance(balance_value, (int, float)) or isinstance(balance_value, bool):
            raise SnapshotError("verified_available_balance_credits must be null or a number")
        if not math.isfinite(float(balance_value)) or float(balance_value) < 0:
            raise SnapshotError("balance must be a finite non-negative number")
        method = balance_doc.get("verification_method")
        if method == "manual_observation":
            observation = balance_doc.get("manual_observation")
            if not isinstance(observation, dict) or not str(observation.get("source", "")).strip():
                raise SnapshotError("manual balance observation must declare its source")
            parse_iso(observation.get("observed_at"))
        elif method == "user_reported":
            observation = balance_doc.get("user_reported")
            if not isinstance(observation, dict):
                raise SnapshotError("user-reported balance requires its typed 'user_reported' provenance object")
            if not str(observation.get("source", "")).strip():
                raise SnapshotError("user-reported balance must declare its source")
            if observation.get("source_precision") != "date":
                raise SnapshotError("user-reported balance must record source_precision 'date'")
            reported = observation.get("reported_date")
            if not isinstance(reported, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reported or ""):
                raise SnapshotError("user-reported balance must record reported_date as YYYY-MM-DD")
            try:
                datetime.fromisoformat(reported)
            except ValueError as exc:
                raise SnapshotError(f"reported_date is not a valid calendar date: {reported!r}") from exc
            if observation.get("exact_report_time_known") is not False:
                raise SnapshotError("date-precision user report must record exact_report_time_known=false")
            parse_iso(observation.get("recorded_at_utc"))
            if float(observation.get("balance_credits", -1)) != float(balance_value):
                raise SnapshotError("user_reported.balance_credits must equal verified_available_balance_credits")
            confidence = balance_doc.get("balance_confidence")
            if not isinstance(confidence, str) or not confidence.strip():
                raise SnapshotError("user-reported balance must carry a distinct balance_confidence")
            if balance_doc.get("balance_status") != "user_reported_not_api_verified":
                raise SnapshotError("user-reported balance must carry balance_status 'user_reported_not_api_verified'")
            if balance_doc.get("balance_is_api_verified") is not False:
                raise SnapshotError("user-reported balance must record balance_is_api_verified=false")
        else:
            raise SnapshotError(
                "numeric balance requires verification_method 'manual_observation' (declared "
                "source and observation time) or 'user_reported' (parent-permitted date-precision "
                "provenance), or a future documented API method"
            )
    if float(balance_doc.get("authorized_ceiling_credits", -1)) != AUTHORIZED_CEILING_CREDITS:
        raise SnapshotError(
            f"authorized_ceiling_credits must equal {AUTHORIZED_CEILING_CREDITS} (the declared remaining ceiling)"
        )
    if float(balance_doc.get("planned_max_allocated_credits", -1)) != PLANNED_MAX_ALLOCATED_CREDITS:
        raise SnapshotError(
            f"planned_max_allocated_credits must equal {PLANNED_MAX_ALLOCATED_CREDITS} (stage envelopes 20/20/100/50)"
        )
    if balance_doc.get("paid_charges_incurred_by_collection") != 0:
        raise SnapshotError("paid_charges_incurred_by_collection must be 0 (collection is read-only)")
    if balance_doc.get("balance_is_api_verified") is not False:
        raise SnapshotError("balance_is_api_verified must be false (no documented balance endpoint)")
    if balance_doc.get("documented_balance_endpoint") is not False:
        raise SnapshotError("documented_balance_endpoint must be false")
    if balance_doc.get("historical_ledger_spend_not_subtracted") is not True:
        raise SnapshotError("snapshot must record that historical spend is not subtracted from the ceiling")

    # Prices: cross-check parsed values against the saved official page bytes.
    prices_doc = documents["prices.json"]
    if prices_doc.get("schema") != SCHEMA_NAME:
        raise SnapshotError("prices.json schema mismatch")
    if prices_doc.get("pricing_source") != PRICING_URL:
        raise SnapshotError("prices.json must cite the official pricing source URL")
    page_bytes = (snapshot_dir / "pricing_page.md").read_bytes()
    if prices_doc.get("page_sha256") != content_sha256(page_bytes):
        raise SnapshotError("prices.json page_sha256 does not match pricing_page.md")
    reparsed = parse_pricing_page(page_bytes.decode("utf-8"))
    for model in required_models:
        rates = prices_doc.get("prices_per_million_tokens", {}).get(model)
        if rates is None:
            raise SnapshotError(f"prices.json lacks prices for required model {model}")
        expected = reparsed.get(model)
        if expected is None or any(
            rates.get(field) != expected.get(field)
            for field in ("input_price_per_million", "cached_input_price_per_million", "output_price_per_million")
        ):
            raise SnapshotError(f"prices.json values for {model} disagree with the saved official pricing page")
        if rates.get("input_price_per_million") is None or rates.get("output_price_per_million") is None:
            raise SnapshotError(f"missing input/output price for {model}")
        for field in ("input_price_per_million", "output_price_per_million"):
            value = rates.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
                raise SnapshotError(f"{model}.{field} must be a finite non-negative number")
    if prices_doc.get("unit_evidence", {}).get("unit_token") != "credits":
        raise SnapshotError("prices.json must evidence the credit unit")

    # Models: availability evidence for the required set; fail on announced shutdown.
    models_doc = documents["models.json"]
    if models_doc.get("schema") != SCHEMA_NAME:
        raise SnapshotError("models.json schema mismatch")
    if models_doc.get("models_endpoint") != MODELS_URL:
        raise SnapshotError("models.json must cite the documented models endpoint")
    availability = models_doc.get("availability") or {}
    for model in required_models:
        entry = availability.get(model)
        if not isinstance(entry, dict):
            raise SnapshotError(f"models.json lacks an availability entry for {model}")
        if entry.get("present") is not True:
            if require_available:
                raise SnapshotError(f"required model {model} is not evidenced as available (present=false)")
            continue
        shutdown = entry.get("shutdown_date")
        if shutdown:
            try:
                shutdown_day = datetime.fromisoformat(str(shutdown).replace("Z", "+00:00")).date()
            except ValueError:
                shutdown_day = None
            if shutdown_day is not None and shutdown_day <= as_of.date():
                raise SnapshotError(f"required model {model} is announced to shut down on/before {as_of.date()}")

    discovery_doc = documents["discovery.json"]
    if discovery_doc.get("schema") != SCHEMA_NAME:
        raise SnapshotError("discovery.json schema mismatch")
    if "endpoint_scan_policy" not in discovery_doc:
        raise SnapshotError("discovery.json must record the no-speculative-scan policy")
    execution = discovery_doc.get("execution")
    if not isinstance(execution, dict):
        raise SnapshotError("discovery.json must record the collection execution block")
    if (
        execution.get("paid_requests") != 0
        or execution.get("inference_calls") != 0
        or execution.get("http_post_requests_made") != 0
    ):
        raise SnapshotError("execution block must record zero paid, POST, and inference requests")
    get_count = execution.get("http_get_requests_made")
    if not isinstance(get_count, int) or isinstance(get_count, bool) or get_count < 0:
        raise SnapshotError("execution block must record a non-negative integer http_get_requests_made")

    # Staleness: newest retrieval timestamp within max_age_days of as_of.
    newest = max(timestamps)
    age_days = (as_of.date() - newest.date()).days
    if age_days > max_age_days:
        raise SnapshotError(
            f"snapshot is stale: newest retrieval {newest.date()} is {age_days} day(s) before {as_of.date()} "
            f"(max_age_days={max_age_days}; stage runners re-verify on the collection date)"
        )

    file_hashes = {name: content_sha256((snapshot_dir / name).read_bytes()) for name in SNAPSHOT_FILES}
    canonical_sha256 = content_sha256(
        canonical_json({"schema": SCHEMA_NAME, "files": file_hashes}).encode("utf8")
    )
    manifest = {
        "schema": SCHEMA_NAME,
        "files": file_hashes,
        "canonical_sha256": canonical_sha256,
        "required_models": list(required_models),
        "pricing_source": PRICING_URL,
        "models_endpoint": MODELS_URL,
        "paid_requests": 0,
        "inference_calls": 0,
        "provenance": {
            "source_state": pinned_source_contract(),
            "api_key_source_kind": documents["models.json"].get("api_key_source_kind", "unknown"),
            "authorization_sha256": documents["models.json"].get("authorization_sha256"),
            "note": "Collect-time provenance; recorded as written, not recomputed by verify.",
        },
    }
    if write_manifest:
        target = snapshot_dir / "evidence_manifest.json"
        with target.open("w", encoding="utf8") as handle:
            handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return manifest


def verify_snapshot(
    snapshot_dir: Path,
    required_models: Sequence[str] = REQUIRED_MODELS,
    *,
    as_of: datetime | None = None,
    max_age_days: int = 0,
) -> dict[str, Any]:
    """Offline verification of a finalized (or finalizable) snapshot; fail-closed."""
    manifest_path = snapshot_dir / "evidence_manifest.json"
    if not manifest_path.is_file():
        finalize_snapshot(snapshot_dir, required_models, as_of=as_of, max_age_days=max_age_days, write_manifest=True)
        manifest_path = snapshot_dir / "evidence_manifest.json"
    stored = json.loads(manifest_path.read_text(encoding="utf8"))
    if stored.get("schema") != SCHEMA_NAME:
        raise SnapshotError("evidence_manifest.json schema mismatch")
    if not isinstance(stored.get("provenance"), dict):
        raise SnapshotError("evidence_manifest.json lacks a provenance block")

    fresh = finalize_snapshot(snapshot_dir, required_models, as_of=as_of, max_age_days=max_age_days, write_manifest=False)
    for field in ("files", "canonical_sha256", "required_models", "pricing_source", "models_endpoint", "paid_requests", "inference_calls"):
        if stored.get(field) != fresh[field]:
            raise SnapshotError(f"evidence_manifest.json field {field} does not match the snapshot (tamper or drift)")
    return stored


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


def collect(
    *,
    evidence_root: Path,
    stamp: str | None,
    api_key: str | None,
    api_key_source_kind: str,
    authorization: str,
    manual_balance: Mapping[str, Any] | None,
    transport: HttpTransport | None = None,
    required_models: Sequence[str] = REQUIRED_MODELS,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Authorized-only snapshot collection. Read-only account checks; no paid calls."""
    if not authorization or not authorization.strip():
        raise EvidenceError("collect requires an explicit --authorize value (collection is parent-gated)")
    moment = moment or utcnow()
    transport = transport or RequestsTransport()
    stamp = stamp or moment.date().isoformat()
    snapshot_dir = evidence_root / stamp
    if snapshot_dir.exists() and any(snapshot_dir.iterdir()):
        raise EvidenceError(
            f"snapshot directory already exists and is non-empty: {snapshot_dir} "
            "(evidence is never overwritten; pass an explicit --stamp such as a UTC time)"
        )
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    errors: list[dict[str, Any]] = []
    authorization_sha256 = content_sha256(authorization.strip().encode("utf8"))

    # 1. Model availability (documented read-only endpoint; no charge).
    try:
        result = check_response(
            "models",
            transport.get(MODELS_URL + "?type=hosted", headers={"Authorization": f"Bearer {api_key}"}),
        )
        raw_models = json.loads(result.text or "")
        items = raw_models.get("data") if isinstance(raw_models, dict) else None
        if not isinstance(items, list):
            raise EvidenceError("models response lacks a data list (possible schema change)")
        records = [sanitize_model_record(item) for item in items if isinstance(item, dict)]
        models_doc = build_models_document(records, required_models, moment)
        models_doc["api_key_source_kind"] = api_key_source_kind
        models_doc["authorization_sha256"] = authorization_sha256
        _write_atomic(snapshot_dir / "models.json", models_doc)
    except (HttpFailure, requests.RequestException) as failure:
        if isinstance(failure, requests.RequestException):
            failure = HttpFailure("models", None, f"network_error:{type(failure).__name__}")
        errors.append(sanitize_error_record("models", failure, moment))
    except (json.JSONDecodeError, EvidenceError) as exc:
        errors.append(
            {
                "item": "models",
                "http_status": None,
                "category": f"response_error:{type(exc).__name__}",
                "retrieved_at_utc": iso_utc(moment),
            }
        )

    # 2. Official public pricing (no auth, no charge).
    try:
        result = check_response("pricing", transport.get(PRICING_URL))
        page_text = result.text or ""
        parsed = parse_pricing_page(page_text)
        _write_atomic(snapshot_dir / "pricing_page.md", page_text)
        _write_atomic(snapshot_dir / "prices.json", build_prices_document(parsed, page_text.encode("utf-8"), moment))
    except (HttpFailure, requests.RequestException) as failure:
        if isinstance(failure, requests.RequestException):
            failure = HttpFailure("pricing", None, f"network_error:{type(failure).__name__}")
        errors.append(sanitize_error_record("pricing", failure, moment))
    except (json.JSONDecodeError, EvidenceError) as exc:
        errors.append(
            {
                "item": "pricing",
                "http_status": None,
                "category": f"response_error:{type(exc).__name__}",
                "retrieved_at_utc": iso_utc(moment),
            }
        )

    # 3. Balance: absence documented; manual/user-reported observation only if supplied.
    # Run-time request accounting: every allowlisted GET attempt the transport made.
    get_count = len(getattr(transport, "calls", None) or [])
    _write_atomic(snapshot_dir / "balance.json", build_balance_document(moment, manual_balance))
    _write_atomic(snapshot_dir / "discovery.json", build_discovery_document(moment, http_get_requests_made=get_count))

    if errors:
        _write_atomic(snapshot_dir / "errors.jsonl", "\n".join(canonical_json(e) for e in errors) + "\n")
        summary = {
            "snapshot": str(snapshot_dir),
            "manifest_written": False,
            "errors": errors,
            "note": "fail-closed: verify will refuse this snapshot until a re-collection succeeds",
        }
        print(canonical_json(summary), flush=True)
        return summary

    manifest = finalize_snapshot(snapshot_dir, required_models, as_of=moment, write_manifest=True, require_available=False)
    if manual_balance is None:
        balance_note = "documented-absence; see balance.json"
    elif manual_balance.get("method") == "user_reported":
        balance_note = "user_reported_balance_recorded; see balance.json"
    else:
        balance_note = "manual_observation_recorded; see balance.json"
    summary = {
        "snapshot": str(snapshot_dir),
        "manifest_written": True,
        "canonical_sha256": manifest["canonical_sha256"],
        "paid_requests": 0,
        "inference_calls": 0,
        "http_get_requests_made": get_count,
        "balance_status": balance_note,
        "required_models": list(required_models),
    }
    print(canonical_json(summary), flush=True)
    return summary


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    # newline="\n": deterministic LF bytes so recorded hashes match on-disk bytes on any platform.
    with tmp.open("w", encoding="utf8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_manual_balance(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        manual = json.loads(path.read_text(encoding="utf8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise EvidenceError(f"balance manual file unreadable: {type(exc).__name__}") from exc
    if not isinstance(manual, dict):
        raise EvidenceError("balance manual file must be a JSON object")
    return manual


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEAL current-account evidence tool (read-only; no inference)")
    sub = parser.add_subparsers(dest="command", required=True)

    collect_cmd = sub.add_parser("collect", help="Authorized-only snapshot collection (requires --authorize)")
    collect_cmd.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    collect_cmd.add_argument("--stamp", type=str, default=None, help="Snapshot directory name (default: UTC date)")
    collect_cmd.add_argument("--api-key-file", type=str, default=None, help="Secret-delivery handle path; read in-process only, never recorded")
    collect_cmd.add_argument(
        "--balance-manual",
        type=Path,
        default=None,
        help=(
            "JSON balance provenance: {method: manual_observation, balance_credits, observed_at, "
            "source[, notes]} or {method: user_reported, balance_credits, source, "
            "source_precision: 'date', reported_date, [verbatim_statement, notes]} "
            "(parent-permitted; never API verified)"
        ),
    )
    collect_cmd.add_argument("--authorize", type=str, required=True, help="Explicit authorization marker (collection is parent-gated)")

    verify_cmd = sub.add_parser("verify", help="Offline validation + manifest check (fail-closed)")
    verify_cmd.add_argument("--evidence-dir", type=Path, required=True)
    verify_cmd.add_argument("--require-models", nargs="+", default=list(REQUIRED_MODELS))
    verify_cmd.add_argument("--max-age-days", type=int, default=0, help="0 = snapshot must be from the same UTC date as this verification")
    verify_cmd.add_argument("--as-of", type=str, default=None, help="ISO timestamp override (testing/replay only)")

    status_cmd = sub.add_parser("status", help="Offline summary of a snapshot directory")
    status_cmd.add_argument("--evidence-dir", type=Path, required=True)

    key_cmd = sub.add_parser("key-source-check", help="Boolean usability report for credential sources")
    key_cmd.add_argument("--api-key-file", type=str, default=None)

    args = parser.parse_args(argv)

    if args.command == "collect":
        api_key, api_key_source_kind = read_api_key(api_key_file=args.api_key_file)
        if api_key is None:
            # Never print the handle path or its contents; report the kind only.
            print(
                canonical_json(
                    {
                        "collect": "refused",
                        "reason": "api_key_unreadable",
                        "api_key_source_kind": api_key_source_kind,
                        "note": "credential source unusable; parent informed without requesting the key",
                    }
                ),
                flush=True,
            )
            return 2
        transport = RequestsTransport()
        summary = collect(
            evidence_root=args.evidence_root,
            stamp=args.stamp,
            api_key=api_key,
            api_key_source_kind=api_key_source_kind,
            authorization=args.authorize,
            manual_balance=_load_manual_balance(args.balance_manual),
            transport=transport,
        )
        return 0 if summary.get("manifest_written") else 1

    if args.command == "verify":
        as_of = parse_iso(args.as_of) if args.as_of else None
        manifest = verify_snapshot(
            args.evidence_dir,
            args.require_models,
            as_of=as_of,
            max_age_days=args.max_age_days,
        )
        print(canonical_json({"verify": "pass", "canonical_sha256": manifest["canonical_sha256"]}), flush=True)
        return 0

    if args.command == "status":
        summary = snapshot_status(args.evidence_dir)
        print(canonical_json(summary), flush=True)
        return 0

    if args.command == "key-source-check":
        print(canonical_json(key_source_status(args.api_key_file)), flush=True)
        return 0

    return 2


def snapshot_status(snapshot_dir: Path) -> dict[str, Any]:
    """Offline summary; no credential material and no network."""
    present = sorted(p.name for p in snapshot_dir.iterdir()) if snapshot_dir.is_dir() else []
    summary: dict[str, Any] = {"snapshot": str(snapshot_dir), "files_present": present}
    models_path = snapshot_dir / "models.json"
    prices_path = snapshot_dir / "prices.json"
    balance_path = snapshot_dir / "balance.json"
    if models_path.is_file():
        try:
            doc = json.loads(models_path.read_text(encoding="utf8"))
            summary["models"] = doc.get("availability")
        except json.JSONDecodeError:
            summary["models"] = "unreadable"
    if prices_path.is_file():
        try:
            doc = json.loads(prices_path.read_text(encoding="utf8"))
            summary["prices"] = doc.get("prices_per_million_tokens")
            summary["pricing_source"] = doc.get("pricing_source")
        except json.JSONDecodeError:
            summary["prices"] = "unreadable"
    if balance_path.is_file():
        try:
            doc = json.loads(balance_path.read_text(encoding="utf8"))
            summary["verified_available_balance_credits"] = doc.get("verified_available_balance_credits")
            summary["balance_status"] = doc.get("balance_status")
            summary["authorized_ceiling_credits"] = doc.get("authorized_ceiling_credits")
            summary["verification_method"] = doc.get("verification_method")
            summary["balance_confidence"] = doc.get("balance_confidence")
        except json.JSONDecodeError:
            summary["balance"] = "unreadable"
    manifest_path = snapshot_dir / "evidence_manifest.json"
    summary["manifest_present"] = manifest_path.is_file()
    if manifest_path.is_file():
        try:
            summary["canonical_sha256"] = json.loads(manifest_path.read_text(encoding="utf8")).get("canonical_sha256")
        except json.JSONDecodeError:
            summary["canonical_sha256"] = None
    return summary


if __name__ == "__main__":
    started = time.perf_counter()
    code = main()
    elapsed = time.perf_counter() - started
    if os.environ.get("SEAL_EVIDENCE_DEBUG_TIMING") == "1":
        print(f"elapsed_seconds={elapsed:.3f}", file=sys.stderr)
    raise SystemExit(code)
