"""Offline tests for the SEAL current-account evidence tool.

No network, no credentials, no paid calls: every HTTP interaction runs through a
fake transport with canned responses. Covers snapshot assembly, sanitization,
fail-closed verification (missing / tampered / stale / hash-mismatch), the
documented balance absence, sanitized error records, and the allowlisted
GET-only transport. The real collection run itself is parent-gated and is never
exercised here.
"""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import scripts.seal_account_evidence as sea

TEST_KEY = "pt_live_DO_NOT_PRINT_9f2c1e77"
AUTH = "SEAL-AUTHORIZED-COLLECTION-2026-11-13"
MOMENT = datetime(2026, 11, 13, 9, 30, 0, tzinfo=timezone.utc)

MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "id": "gemma-4-31b-it",
            "created": 1750000000,
            "object": "model",
            "owned_by": "platform",
            "display_name": "Gemma 4 31B IT",
            "shutdown_date": None,
        },
        {
            "id": "qwen3.5-397b",
            "created": 1750000000,
            "object": "model",
            "owned_by": "platform",
            "display_name": "Qwen 3.5 397B",
            "shutdown_date": None,
        },
        {
            "id": "glm-5.2",
            "created": 1750000000,
            "object": "model",
            "owned_by": "platform",
            "display_name": "GLM 5.2",
            "shutdown_date": None,
        },
    ],
}

# Faithful excerpt of the official pricing page (rows plus the unit sentence).
PRICING_PAGE = """---
title: Models | Adaption
---

Explore the hosted models available for direct Responses API calls.

## Pricing

Prices are in credits per 1 million tokens. Cached input prices are shown in parentheses. The cached rate applies only to cached input tokens; all other input tokens use the standard rate.

| Model ID | Input credits (cached) | Output credits |
| --- | ---: | ---: |
| [`gemma-4-31b-it`](https://huggingface.co/google/gemma-4-31B-it) | 0.09 (0.05) | 0.34 |
| [`glm-5.2`](https://huggingface.co/zai-org/GLM-5.2) | 1.40 (0.26) | 4.40 |
| [`qwen3.5-397b`](https://huggingface.co/Qwen/Qwen3.5-397B-A17B) | 0.55 (0.225) | 3.50 |

## Configurations

| Model ID | Context length (tokens) | Input modalities |
| --- | ---: | --- |
| [`gemma-4-31b-it`](https://huggingface.co/google/gemma-4-31B-it) | 256,000 | Text, image |
| [`qwen3.5-397b`](https://huggingface.co/Qwen/Qwen3.5-397B-A17B) | 131,072 | Text, image |
"""

EXPECTED_PRICES = {
    "gemma-4-31b-it": {
        "input_price_per_million": 0.09,
        "cached_input_price_per_million": 0.05,
        "output_price_per_million": 0.34,
    },
    "qwen3.5-397b": {
        "input_price_per_million": 0.55,
        "cached_input_price_per_million": 0.225,
        "output_price_per_million": 3.50,
    },
}


class FakeTransport(sea.HttpTransport):
    """Canned responses keyed by base URL; records every call it receives."""

    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []
        self.header_log: list[dict[str, str]] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None) -> sea.TransportResult:
        self.calls.append(("GET", url))
        self.header_log.append(dict(headers or {}))
        base = url.split("?", 1)[0]
        if base not in self.responses:
            raise sea.TransportBlocked(f"unexpected URL in test: {base}")
        outcome = self.responses[base]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def default_transport(**overrides) -> FakeTransport:
    responses = {
        sea.MODELS_URL: sea.TransportResult(200, json.dumps(MODELS_RESPONSE)),
        sea.PRICING_URL: sea.TransportResult(200, PRICING_PAGE),
    }
    responses.update(overrides)
    return FakeTransport(responses)


class SnapshotTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "account_evidence"
        self.snapshot = self.root / "2026-11-13"

    def collect(self, *, transport=None, manual=None, authorize: str = AUTH, key: str | None = TEST_KEY):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            summary = sea.collect(
                evidence_root=self.root,
                stamp="2026-11-13",
                api_key=key,
                api_key_source_kind="api_key_file",
                authorization=authorize,
                manual_balance=manual,
                transport=transport if transport is not None else default_transport(),
                moment=MOMENT,
            )
        return summary, stdout.getvalue()

    def verify(self, *, as_of=MOMENT, max_age_days: int = 0, require_models=sea.REQUIRED_MODELS):
        return sea.verify_snapshot(
            self.snapshot,
            require_models,
            as_of=as_of,
            max_age_days=max_age_days,
        )

    def read(self, name: str):
        return json.loads((self.snapshot / name).read_text(encoding="utf8"))


class CollectionTests(SnapshotTestCase):
    def test_collect_writes_complete_snapshot_and_manifest(self):
        summary, _ = self.collect()
        self.assertTrue(summary["manifest_written"])
        self.assertEqual(summary["paid_requests"], 0)
        self.assertEqual(summary["inference_calls"], 0)
        for name in sea.SNAPSHOT_FILES:
            self.assertTrue((self.snapshot / name).is_file(), name)
        self.assertTrue((self.snapshot / "evidence_manifest.json").is_file())

        manifest = self.read("evidence_manifest.json")
        recomputed = sea.content_sha256(
            sea.canonical_json({"schema": sea.SCHEMA_NAME, "files": manifest["files"]}).encode("utf8")
        )
        self.assertEqual(manifest["canonical_sha256"], recomputed)
        self.assertEqual(summary["canonical_sha256"], recomputed)
        self.assertEqual(manifest["paid_requests"], 0)
        self.assertEqual(manifest["inference_calls"], 0)

        models = self.read("models.json")
        for model in sea.REQUIRED_MODELS:
            self.assertTrue(models["availability"][model]["present"], model)
        self.assertEqual(models["required_models"], list(sea.REQUIRED_MODELS))
        self.assertEqual(models["requested_type"], "hosted")

        prices = self.read("prices.json")
        self.assertEqual(prices["prices_per_million_tokens"], EXPECTED_PRICES)
        self.assertEqual(prices["pricing_source"], sea.PRICING_URL)
        page = (self.snapshot / "pricing_page.md").read_text(encoding="utf-8")
        self.assertEqual(sea.content_sha256(page.encode("utf-8")), prices["page_sha256"])

        # Verification passes on the collection date itself.
        manifest_verified = self.verify()
        self.assertEqual(manifest_verified["canonical_sha256"], manifest["canonical_sha256"])

    def test_collect_performs_only_allowlisted_gets(self):
        transport = default_transport()
        self.collect(transport=transport)
        self.assertEqual(
            transport.calls,
            [
                ("GET", sea.MODELS_URL + sea.MODELS_REQUIRED_QUERY),
                ("GET", sea.PRICING_URL),
            ],
        )
        # The paid inference endpoint is never requested, and only GET is used.
        for _, url in transport.calls:
            self.assertNotIn("responses", url)
        self.assertTrue(all(method == "GET" for method, _ in transport.calls))

    def test_collect_sends_bearer_header_without_persisting_it(self):
        transport = default_transport()
        self.collect(transport=transport)
        self.assertEqual(transport.header_log[0].get("Authorization"), f"Bearer {TEST_KEY}")
        # The key value must not appear anywhere in the snapshot.
        for path in self.snapshot.iterdir():
            self.assertNotIn(TEST_KEY.encode("utf-8"), path.read_bytes(), path.name)

    def test_missing_required_model_recorded_then_verify_fails(self):
        missing_qwen = {"object": "list", "data": [MODELS_RESPONSE["data"][0]]}
        summary, _ = self.collect(transport=default_transport(**{sea.MODELS_URL: sea.TransportResult(200, json.dumps(missing_qwen))}))
        self.assertTrue(summary["manifest_written"])  # collection succeeded
        models = self.read("models.json")
        self.assertFalse(models["availability"]["qwen3.5-397b"]["present"])
        self.assertTrue(models["availability"]["gemma-4-31b-it"]["present"])
        with self.assertRaisesRegex(sea.SnapshotError, "qwen3.5-397b"):
            self.verify()

    def test_sensitive_fields_stripped_from_models(self):
        polluted = {
            "object": "list",
            "data": [
                {
                    "id": "gemma-4-31b-it",
                    "object": "model",
                    "owned_by": "org_private_label",
                    "organization_id": "org_9182",
                    "account_email": "owner@example.net",
                    "display_name": "Gemma 4 31B IT",
                },
                {
                    "id": "qwen3.5-397b",
                    "object": "model",
                    "owned_by": "platform",
                    "display_name": "Qwen 3.5 397B",
                },
            ],
        }
        self.collect(transport=default_transport(**{sea.MODELS_URL: sea.TransportResult(200, json.dumps(polluted))}))
        text = (self.snapshot / "models.json").read_text(encoding="utf8")
        self.assertNotIn("organization_id", text)
        self.assertNotIn("account_email", text)
        self.assertNotIn("org_9182", text)
        self.assertNotIn("owner@example.net", text)
        self.assertIn("<non-platform-owner-redacted>", text)
        models = self.read("models.json")
        self.assertEqual(models["availability"]["gemma-4-31b-it"]["owned_by"], "<non-platform-owner-redacted>")
        self.assertEqual(models["availability"]["qwen3.5-397b"]["owned_by"], "platform")

    def test_http_error_recorded_without_sensitive_details(self):
        leaky_body = json.dumps({"error": {"message": "account_id=org_9182 balance=10"}})
        summary, _ = self.collect(
            transport=default_transport(**{sea.MODELS_URL: sea.TransportResult(500, leaky_body)})
        )
        self.assertFalse(summary["manifest_written"])
        self.assertFalse((self.snapshot / "models.json").is_file())
        # Pricing and balance artifacts still exist, but the snapshot fails closed.
        self.assertTrue((self.snapshot / "prices.json").is_file())
        errors = (self.snapshot / "errors.jsonl").read_text(encoding="utf8").strip().splitlines()
        self.assertEqual(len(errors), 1)
        record = json.loads(errors[0])
        self.assertEqual(record["item"], "models")
        self.assertEqual(record["http_status"], 500)
        self.assertEqual(record["category"], "http_server_error")
        self.assertNotIn("org_9182", errors[0])
        self.assertNotIn("balance=10", errors[0])
        with self.assertRaises(sea.SnapshotError):
            self.verify()

    def test_auth_error_recorded_sanitized(self):
        summary, _ = self.collect(
            transport=default_transport(**{sea.MODELS_URL: sea.TransportResult(401, '{"error":"bad key"}')})
        )
        self.assertFalse(summary["manifest_written"])
        record = json.loads((self.snapshot / "errors.jsonl").read_text(encoding="utf8").strip())
        self.assertEqual(record["http_status"], 401)
        self.assertEqual(record["category"], "auth_error")

    def test_network_error_recorded_without_exception_text(self):
        boom = requests.ConnectionError("proxy leak http://10.0.0.1:8080")
        summary, _ = self.collect(transport=default_transport(**{sea.MODELS_URL: boom}))
        self.assertFalse(summary["manifest_written"])
        record = json.loads((self.snapshot / "errors.jsonl").read_text(encoding="utf8").strip())
        self.assertEqual(record["item"], "models")
        self.assertIsNone(record["http_status"])
        self.assertEqual(record["category"], "network_error:ConnectionError")
        self.assertNotIn("proxy leak", json.dumps(record))
        with self.assertRaises(sea.SnapshotError):
            self.verify()

    def test_malformed_models_json_recorded_sanitized(self):
        summary, _ = self.collect(
            transport=default_transport(**{sea.MODELS_URL: sea.TransportResult(200, "<html>not json</html>")})
        )
        self.assertFalse(summary["manifest_written"])
        record = json.loads((self.snapshot / "errors.jsonl").read_text(encoding="utf8").strip())
        self.assertEqual(record["category"], "response_error:JSONDecodeError")
        self.assertNotIn("html", record["category"])

    def test_authorization_is_required(self):
        transport = default_transport()
        with self.assertRaisesRegex(sea.EvidenceError, "authorize"):
            self.collect(transport=transport, authorize="  ")
        self.assertEqual(transport.calls, [])

    def test_existing_snapshot_never_overwritten(self):
        self.collect()
        with self.assertRaisesRegex(sea.EvidenceError, "never overwritten"):
            self.collect()

    def test_manual_balance_observation_recorded_with_provenance(self):
        manual = {
            "balance_credits": 1200.5,
            "observed_at": MOMENT.isoformat(),
            "source": "Adaption app console, read by the authorized account holder",
        }
        self.collect(manual=manual)
        balance = self.read("balance.json")
        self.assertEqual(balance["verified_available_balance_credits"], 1200.5)
        self.assertEqual(balance["verification_method"], "manual_observation")
        self.assertEqual(balance["manual_observation"]["source"], manual["source"])
        self.assertTrue(balance["historical_ledger_spend_not_subtracted"])
        self.verify()

    def test_manual_balance_requires_source(self):
        manual = {"balance_credits": 1200.5, "observed_at": MOMENT.isoformat()}
        with self.assertRaisesRegex(sea.EvidenceError, "source"):
            self.collect(manual=manual)

    def test_manual_balance_requires_valid_timestamp(self):
        manual = {"balance_credits": 1200.5, "observed_at": "not-a-date", "source": "console"}
        with self.assertRaisesRegex(sea.EvidenceError, "ISO-8601"):
            self.collect(manual=manual)

    def test_user_reported_balance_recorded_with_provenance(self):
        manual = {
            "method": "user_reported",
            "balance_credits": 1490,
            "source": "SEAL user conversation in this task thread (account holder's statement)",
            "source_precision": "date",
            "reported_date": "2026-11-13",
            "verbatim_statement": "i have 1490 adaptionai credits",
        }
        self.collect(manual=manual)
        balance = self.read("balance.json")
        self.assertEqual(balance["verified_available_balance_credits"], 1490.0)
        self.assertEqual(balance["balance_status"], "user_reported_not_api_verified")
        self.assertEqual(balance["verification_method"], "user_reported")
        self.assertEqual(balance["balance_confidence"], "user_reported_not_api_verified")
        self.assertIs(balance["balance_is_api_verified"], False)
        self.assertIn("no account, billing", balance["balance_status_detail"])
        observation = balance["user_reported"]
        self.assertEqual(observation["source_precision"], "date")
        self.assertEqual(observation["reported_date"], "2026-11-13")
        self.assertIs(observation["exact_report_time_known"], False)
        self.assertIn("recorded_at_utc", observation)  # recording time kept separate from the unknown report time
        self.assertIn("explicitly", balance["user_reported_permission"])
        self.assertEqual(balance["authorized_ceiling_credits"], 1490.0)
        self.assertEqual(balance["planned_max_allocated_credits"], 190.0)
        self.assertEqual(balance["paid_charges_incurred_by_collection"], 0)
        self.verify()

    def test_user_reported_requires_source(self):
        manual = {
            "method": "user_reported",
            "balance_credits": 1490,
            "source_precision": "date",
            "reported_date": "2026-11-13",
        }
        with self.assertRaisesRegex(sea.EvidenceError, "source"):
            self.collect(manual=manual)

    def test_user_reported_requires_date_precision(self):
        manual = {
            "method": "user_reported",
            "balance_credits": 1490,
            "source": "conversation",
            "source_precision": "minute",
            "reported_date": "2026-11-13",
        }
        with self.assertRaisesRegex(sea.EvidenceError, "source_precision"):
            self.collect(manual=manual)

    def test_user_reported_requires_valid_reported_date(self):
        manual = {
            "method": "user_reported",
            "balance_credits": 1490,
            "source": "conversation",
            "source_precision": "date",
            "reported_date": "2026-13-45",
        }
        with self.assertRaisesRegex(sea.EvidenceError, "reported_date"):
            self.collect(manual=manual)

    def test_discovery_records_actual_get_count_and_zero_paid(self):
        transport = default_transport()
        self.collect(transport=transport)
        execution = self.read("discovery.json")["execution"]
        self.assertEqual(execution["http_get_requests_made"], 2)
        self.assertEqual(execution["http_get_requests_made"], len(transport.calls))
        self.assertEqual(execution["http_post_requests_made"], 0)
        self.assertEqual(execution["paid_requests"], 0)
        self.assertEqual(execution["inference_calls"], 0)

    def test_key_source_check_booleans(self):
        status = sea.key_source_status(api_key_file=str(Path(self.tmp.name) / "does_not_exist"))
        self.assertFalse(status["key_file_readable"])
        handle = Path(self.tmp.name) / "handle"
        handle.write_text(TEST_KEY, encoding="utf-8")
        status = sea.key_source_status(api_key_file=str(handle))
        self.assertTrue(status["key_file_readable"])
        self.assertEqual(set(status), {"env_key_present", "key_file_readable"})


class BalanceDocumentationTests(SnapshotTestCase):
    def test_balance_absence_documented_not_fabricated(self):
        self.collect()
        balance = self.read("balance.json")
        self.assertIsNone(balance["verified_available_balance_credits"])
        self.assertEqual(balance["balance_status"], "no_documented_balance_endpoint")
        self.assertIn("no account, billing", balance["balance_status_detail"])
        self.assertEqual(balance["authorized_ceiling_credits"], 1490.0)
        self.assertIn("not a verified available balance", balance["authorized_ceiling_basis"])
        self.assertEqual(balance["historical_ledger_spent_credits_reference_only"], 4.10737974)
        self.assertTrue(balance["historical_ledger_spend_not_subtracted"])
        self.assertNotIn("verified_balance_minus", json.dumps(balance))
        self.verify()  # null balance with explicit status is acceptable

    def test_numeric_balance_without_provenance_fails_closed(self):
        self.collect()
        balance_path = self.snapshot / "balance.json"
        doc = json.loads(balance_path.read_text(encoding="utf8"))
        doc["verified_available_balance_credits"] = 999.0
        balance_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        # Remove the manifest so verification reaches the balance rule itself.
        (self.snapshot / "evidence_manifest.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "manual_observation"):
            sea.verify_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT)

    def test_ceiling_mismatch_fails_closed(self):
        self.collect()
        balance_path = self.snapshot / "balance.json"
        doc = json.loads(balance_path.read_text(encoding="utf8"))
        doc["authorized_ceiling_credits"] = 1495.819  # historical balance, not the ceiling
        balance_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        (self.snapshot / "evidence_manifest.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "1490"):
            sea.verify_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT)

    def test_discovery_documents_balance_absence_and_no_speculative_scan(self):
        self.collect()
        discovery = self.read("discovery.json")
        self.assertIn("No speculative", discovery["endpoint_scan_policy"])
        purposes = {entry["purpose"]: entry for entry in discovery["endpoints"]}
        self.assertEqual(purposes["balance"]["status"], "no_documented_endpoint")
        self.assertEqual(
            purposes["inference_responses"]["status"],
            "excluded_paid_inference_not_called_by_this_tool",
        )
        self.assertEqual(purposes["inference_responses"]["paid"], True)
        self.assertEqual(purposes["model_availability"]["paid"], False)
        for entry in discovery["endpoints"]:
            if entry["purpose"] == "inference_responses":
                self.assertTrue(entry.get("source"), entry["purpose"])
            else:
                self.assertTrue(entry.get("source_urls"), entry["purpose"])

    def test_planned_allocation_and_no_paid_charges_recorded(self):
        self.collect()
        balance = self.read("balance.json")
        self.assertEqual(balance["planned_max_allocated_credits"], 190.0)
        self.assertIn("stage envelopes", balance["planned_max_allocated_basis"])
        self.assertEqual(balance["paid_charges_incurred_by_collection"], 0)
        self.assertIs(balance["balance_is_api_verified"], False)
        self.assertIs(balance["documented_balance_endpoint"], False)

    def test_planned_allocation_mismatch_fails_closed(self):
        self.collect()
        balance_path = self.snapshot / "balance.json"
        doc = json.loads(balance_path.read_text(encoding="utf8"))
        doc["planned_max_allocated_credits"] = 200.0
        balance_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        (self.snapshot / "evidence_manifest.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "190"):
            sea.verify_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT)

    def test_verify_refuses_nonzero_paid_in_execution(self):
        self.collect()
        discovery_path = self.snapshot / "discovery.json"
        doc = json.loads(discovery_path.read_text(encoding="utf8"))
        doc["execution"]["paid_requests"] = 1
        discovery_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        (self.snapshot / "evidence_manifest.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "zero paid"):
            sea.verify_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT)

    def test_verify_refuses_user_reported_without_confidence(self):
        manual = {
            "method": "user_reported",
            "balance_credits": 1490,
            "source": "conversation",
            "source_precision": "date",
            "reported_date": "2026-11-13",
        }
        self.collect(manual=manual)
        balance_path = self.snapshot / "balance.json"
        doc = json.loads(balance_path.read_text(encoding="utf8"))
        doc.pop("balance_confidence")
        balance_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        (self.snapshot / "evidence_manifest.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "balance_confidence"):
            sea.verify_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT)

    def test_verify_refuses_user_reported_value_mismatch(self):
        manual = {
            "method": "user_reported",
            "balance_credits": 1490,
            "source": "conversation",
            "source_precision": "date",
            "reported_date": "2026-11-13",
        }
        self.collect(manual=manual)
        balance_path = self.snapshot / "balance.json"
        doc = json.loads(balance_path.read_text(encoding="utf8"))
        doc["verified_available_balance_credits"] = 1489.0
        balance_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        (self.snapshot / "evidence_manifest.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "equal"):
            sea.verify_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT)


class VerificationTests(SnapshotTestCase):
    def setUp(self):
        super().setUp()
        self.collect()

    def test_verify_passes_and_is_idempotent(self):
        first = self.verify()
        second = self.verify()
        self.assertEqual(first["canonical_sha256"], second["canonical_sha256"])

    def test_verify_detects_tampered_file(self):
        prices_path = self.snapshot / "prices.json"
        doc = json.loads(prices_path.read_text(encoding="utf8"))
        doc["prices_per_million_tokens"]["qwen3.5-397b"]["output_price_per_million"] = 9.99
        prices_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf8")
        with self.assertRaisesRegex(sea.SnapshotError, "disagree|match"):
            self.verify()

    def test_verify_detects_hash_mismatch(self):
        manifest_path = self.snapshot / "evidence_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        manifest["files"]["prices.json"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf8")
        with self.assertRaisesRegex(sea.SnapshotError, "files"):
            self.verify()

    def test_verify_detects_missing_file(self):
        (self.snapshot / "models.json").unlink()
        with self.assertRaisesRegex(sea.SnapshotError, "models.json"):
            self.verify()

    def test_verify_detects_missing_manifest(self):
        (self.snapshot / "evidence_manifest.json").unlink()
        manifest = self.verify()  # regenerated deterministically
        self.assertEqual(manifest["paid_requests"], 0)

    def test_verify_staleness_fail_closed(self):
        with self.assertRaisesRegex(sea.SnapshotError, "stale"):
            self.verify(as_of=MOMENT + timedelta(days=2))
        with self.assertRaisesRegex(sea.SnapshotError, "stale"):
            self.verify(as_of=MOMENT + timedelta(days=2), max_age_days=1)

    def test_verify_max_age_boundary(self):
        self.verify(as_of=MOMENT + timedelta(days=1), max_age_days=1)

    def test_verify_refuses_double_finalize(self):
        with self.assertRaisesRegex(sea.SnapshotError, "frozen once"):
            sea.finalize_snapshot(self.snapshot, sea.REQUIRED_MODELS, as_of=MOMENT, write_manifest=True)

    def test_source_state_records_extension_drift(self):
        manifest = self.verify()
        state = manifest["provenance"]["source_state"]
        pinned = state["pinned_sha256"]
        self.assertEqual(
            set(pinned),
            {
                "src/integrity.py",
                "src/live_fixtures.py",
                "src/live_study.py",
                "src/live_workers.py",
                "src/live_analysis.py",
            },
        )
        expected_drift = sorted(
            path for path, digest in state["current_sha256"].items()
            if digest is not None and digest != pinned[path]
        )
        self.assertEqual(state["drift"], expected_drift)
        self.assertIn(
            "original-five-module assumption",
            state["note"],
        )

    def test_verify_cli_roundtrip(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = sea.main(
                [
                    "verify",
                    "--evidence-dir",
                    str(self.snapshot),
                    "--as-of",
                    MOMENT.isoformat(),
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn('"verify":"pass"', stdout.getvalue().replace(" ", ""))


class PricingParsingTests(unittest.TestCase):
    def test_parse_extractes_required_models(self):
        parsed = sea.parse_pricing_page(PRICING_PAGE)
        for model, expected in EXPECTED_PRICES.items():
            self.assertEqual(parsed[model], expected)
        # Extra catalog rows parse too, but only required models enter the snapshot.
        self.assertEqual(parsed["glm-5.2"]["output_price_per_million"], 4.40)

    def test_parse_requires_unit_phrase(self):
        with self.assertRaisesRegex(sea.EvidenceError, "credit unit"):
            sea.parse_pricing_page(PRICING_PAGE.replace("credits per 1 million tokens", "tokens per dollar"))

    def test_parse_requires_rows(self):
        with self.assertRaisesRegex(sea.EvidenceError, "no rows"):
            sea.parse_pricing_page("Prices are in credits per 1 million tokens.\n\n| a | b | c |\n")

    def test_transport_blocks_non_allowlisted_requests(self):
        transport = sea.RequestsTransport(session=requests.Session())
        with self.assertRaises(sea.TransportBlocked):
            transport.get("https://evil.example/api/v1/models")
        with self.assertRaises(sea.TransportBlocked):
            transport.get(sea.RESPONSES_URL + "?x=1")
        with self.assertRaisesRegex(sea.TransportBlocked, "type=hosted"):
            transport.get(sea.MODELS_URL + "?type=all")


if __name__ == "__main__":
    unittest.main()
