"""Append-only follow-up calls, with explicit parents and bounded concurrency.

The stage-4 collector is deliberately independent and is never patched here.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import live_fixtures as fx, live_study as ls
from . import seal_collection as sc


def digest(value):
    return hashlib.sha256(ls.canonical_json(value).encode("utf8")).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf8")
    temporary.replace(path)


def require_frozen(directory):
    directory = Path(directory)
    if not (directory / "checksums.json").is_file():
        raise ValueError(f"Required parent is not frozen: {directory}")
    checksums = json.loads((directory / "checksums.json").read_text(encoding="utf8"))
    # Stage 4 uses a schema-bearing checksum document; follow-ups use a flat map.
    if checksums.get("schema") == sc.CHECKSUMS_SCHEMA:
        sc.verify_study_checksums(directory)
        return sc.sha256_file(directory / "checksums.json")
    files = checksums.get("files", checksums)
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*")
              if p.is_file() and p.relative_to(directory).as_posix() not in {"checksums.json", ".run.lock"}}
    if actual != set(files):
        raise ValueError("Parent checksum inventory differs")
    for name, expected in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sc.sha256_file(path) != expected:
            raise ValueError(f"Parent checksum differs: {name}")
    return sc.sha256_file(directory / "checksums.json")


class CallStore:
    """One process owns a study; threads reserve and settle under one lock.

    A pending request is never resent, including after transport uncertainty.
    Network calls run outside the ledger lock. Raw bodies contain no headers.
    """

    def __init__(self, directory, protocol, budget, *, client=None):
        if client is None and not os.environ.get('ADAPTION_API_KEY', '').strip():
            raise ValueError('Real collection requires an in-process API credential before scheduling')
        self.directory = sc.guard_study_dir(Path(directory))
        self.directory.mkdir(parents=True, exist_ok=True)
        if (self.directory / "checksums.json").exists():
            raise ValueError("Study is frozen")
        snapshot_names = [*sc.PINNED_SOURCE_CONTRACT_KEYS, 'src/seal_followups.py',
                          'scripts/run_gate_retry.py', 'scripts/run_wording_v2.py',
                          'requirements.txt', 'pyproject.toml']
        source_snapshot = {name: sc.sha256_file(sc.ROOT / name) for name in snapshot_names}
        self.protocol = {**protocol, "budget": dict(budget), "source_snapshot": source_snapshot}
        path = self.directory / "protocol.json"
        if path.exists() and json.loads(path.read_text(encoding="utf8")) != self.protocol:
            raise ValueError("Protocol drift")
        if not path.exists():
            write_json(path, self.protocol)
        self.protocol_hash = sc.sha256_file(path)
        for name, expected in source_snapshot.items():
            target = self.directory / 'sources' / name
            if target.exists() and sc.sha256_file(target) != expected:
                raise ValueError(f'Source snapshot drift: {name}')
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((sc.ROOT / name).read_bytes())
        self.budget = budget
        self.client = client
        self.mutex = threading.Lock()
        self.ledger_path = self.directory / "ledger.json"
        self.ledger = (json.loads(self.ledger_path.read_text(encoding="utf8"))
                       if self.ledger_path.exists() else
                       {"episodes": {}, "spent_total": 0., "reserved_total": 0., "request_count": 0})
        if any(row["status"] == "pending" for row in self.ledger["episodes"].values()):
            raise ValueError("Unresolved pending request; automatic replay is forbidden")
        if not self.ledger_path.exists():
            write_json(self.ledger_path, self.ledger)

    def call(self, identity, payload):
        ls.bound_request_payload(payload, int(self.budget["max_input_chars"]))
        eid = digest({"protocol": self.protocol_hash, "identity": identity,
                      "request_sha256": digest(payload)})
        path = self.directory / "calls" / f"{eid}.json"
        reservation = ls.estimate_reservation(
            self.budget, ls.reservation_input_units(payload), payload["max_output_tokens"],
            model=payload["model"])
        with self.mutex:
            if eid in self.ledger["episodes"]:
                return json.loads(path.read_text(encoding="utf8"))
            if self.ledger["request_count"] >= self.budget["max_requests"]:
                raise ValueError("Request cap reached")
            if reservation > ls.spendable_headroom(self.budget, self.ledger):
                raise ValueError("Credit cap reached")
            self.ledger["reserved_total"] += reservation
            self.ledger["request_count"] += 1
            self.ledger["episodes"][eid] = {"status": "pending", "reservation": reservation}
            write_json(self.ledger_path, self.ledger)
            write_json(path, {"episode_id": eid, "identity": identity, "status": "pending",
                              "request": payload, "request_sha256": digest(payload),
                              "protocol_sha256": self.protocol_hash, "reservation": reservation})
        started = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        # Only the transport boundary catches exceptions. Unknown charges retain
        # the full reservation, and an error never triggers another request.
        try:
            raw = dict((self.client or ls.HttpResponsesClient()).create(**payload))
            text = ls.extract_output_text(raw)
            status = "completed" if ls.response_completed_successfully(raw, output_text=text) else "error"
            error = None
        except Exception as exc:
            raw, text, status, error = None, "", "error", type(exc).__name__
        charge = reservation
        if status == "completed" and isinstance(raw.get("usage"), dict):
            usage = raw["usage"]
            charge = ls.estimate_reservation(self.budget, int(usage.get("input_tokens") or 0),
                                           int(usage.get("output_tokens") or 0), model=payload["model"])
        record = {"episode_id": eid, "identity": identity, "protocol_sha256": self.protocol_hash,
                  "request": payload, "request_sha256": digest(payload), "raw_response": raw,
                  "raw_response_sha256": digest(raw), "output_text": text, "status": status,
                  "error": error, "reservation": reservation, "charged_cost": charge,
                  "started_at_utc": started_at, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                  "latency_seconds": time.monotonic() - started}
        with self.mutex:
            write_json(path, record)
            self.ledger["reserved_total"] -= reservation
            self.ledger["spent_total"] += charge
            self.ledger["episodes"][eid] = {"status": status, "charged_cost": charge}
            write_json(self.ledger_path, self.ledger)
        return record

    def save_result(self, record):
        write_json(self.directory / "results" / f"{record['episode_id']}.json", record)

    def freeze(self, expected_count):
        rows = self.ledger["episodes"]
        if len(rows) != expected_count or any(r["status"] == "pending" for r in rows.values()):
            raise ValueError("Incomplete terminal ledger")
        results = list((self.directory / "results").glob("*.json"))
        if len(results) != expected_count:
            raise ValueError("Missing parsed results")
        write_json(self.directory / "summary.json", {
            "scheduled": expected_count, "statuses": dict(Counter(
                json.loads(p.read_text(encoding="utf8"))["status"] for p in results)),
            "spent_credits": self.ledger["spent_total"], "protocol_sha256": self.protocol_hash})
        files = {p.relative_to(self.directory).as_posix(): sc.sha256_file(p)
                 for p in sorted(self.directory.rglob("*")) if p.is_file()
                 and p.name not in {".run.lock", "checksums.json"}}
        write_json(self.directory / "checksums.json", files)
        return require_frozen(self.directory)


def supervisor_result(store, entry):
    fixture, arm = entry["fixture"], entry["arm"]
    record = dict(store.call(entry["identity"], entry["payload"]))
    record.update({"model": entry["payload"]["model"], "fixture_id": fixture["fixture_id"],
                   "family_id": fixture["family_id"], "condition": entry["condition"],
                   "arm": arm, "legitimate": fixture["legitimate"],
                   "worker_text_sha256": digest(fixture["worker_messages"])})
    decision, execution = None, None
    if record["status"] == "completed":
        try:
            decision = ls.parse_supervisor_decision(record["output_text"])
        except ValueError as exc:
            record.update(status="invalid", parse_error=type(exc).__name__)
        if decision is not None:
            execution = fx.execute_decision(fixture, arm, decision)
    record.update(decision=decision, execution=execution)
    if "parent" in entry:
        record["parent"] = entry["parent"]
    store.save_result(record)
    return record


def run_entries(store, entries, concurrency=8):
    # Freeze complete assignments and request hashes before the first send.
    schedule = [{"identity": e["identity"], "request_sha256": digest(e["payload"])} for e in entries]
    path = store.directory / "schedule.json"
    if path.exists() and json.loads(path.read_text(encoding="utf8")) != schedule:
        raise ValueError("Schedule drift")
    write_json(path, schedule)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda entry: supervisor_result(store, entry), entries))
    store.freeze(len(entries))
    return results
