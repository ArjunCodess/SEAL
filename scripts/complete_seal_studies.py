"""Continue the active, authorized SEAL collection after the blind collector exits.

This runner sends no request until its checksum-frozen dependency exists. It
does not resubmit pending calls or rerun an already frozen collection phase.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import seal_collection as sc, seal_followups as follow
from scripts import run_gate_retry, run_wording_v2, analyze_seal_followups


def announce(phase, result):
    if isinstance(result, dict) and 'episode_ids' in result:
        result = {key: result[key] for key in ('protocol_name', 'scheduled_episode_count',
                  'status_counts', 'checksums_sha256')}
    print(json.dumps({"phase": phase, "result": result}, default=str), flush=True)


def run(args):
    if not os.environ.get("ADAPTION_API_KEY"):
        raise ValueError("Missing in-process inference credential")
    result_file = Path(args.after_blind_summary)
    last_report = 0.
    while not result_file.is_file():
        if time.monotonic() - last_report > 60:
            ledger_path = ROOT / "data/study_blind_provenance/ledger.json"
            if ledger_path.is_file():
                ledger = sc.read_json(ledger_path)
                announce("waiting_for_active_blind_collector", {
                    "terminal": ledger["request_count"], "spent_credits": ledger["spent_total"]})
            last_report = time.monotonic()
        time.sleep(5)
    blind = ROOT / "data/study_blind_provenance"
    from scripts.freeze_blind_evidence import freeze as freeze_blind
    announce("blind_freeze", freeze_blind(blind))
    announce("blind_comparison", sc.build_comparison(study_dir=blind))
    # These two independent short papers can already use the complete blind
    # ledger before the later studies finish. Their own generators verify it.
    # Use the public CLI instead of reconstructing this generator's independent
    # file arguments; no credentials or network are involved in its subprocess.
    import subprocess
    process = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_seal_venues.py'), '--compile'],
                             cwd=ROOT, capture_output=True, text=True, errors='replace')
    announce('agentverify', {'compiled': process.returncode == 0, 'error': process.stderr[-1500:] if process.returncode else None})
    process = subprocess.run([sys.executable, str(ROOT / "scripts/prepare_seal_admit.py"),
                              "--compile", "--include-blind", "--blind-dir", str(blind)],
                             cwd=ROOT, capture_output=True, text=True, errors="replace")
    announce("admit", {"compiled": process.returncode == 0, 'error': process.stderr[-1500:] if process.returncode else None})
    budget = ROOT / "configs/seal/stage4_budget.json"
    retry_dir = ROOT / "data/study_gate_retry"
    if not (retry_dir / "checksums.json").is_file():
        announce("retry_preflight", run_gate_retry.run(SimpleNamespace(
            command="dry-run", parent=blind, study_dir=retry_dir, budget_config=budget)))
        announce("retry_collection", run_gate_retry.run(SimpleNamespace(
            command="collect", parent=blind, study_dir=retry_dir, budget_config=budget)))
    follow.require_frozen(retry_dir)
    wording = ROOT / "data/study_wording_v2"
    word_args = SimpleNamespace(study_dir=wording, budget_config=budget)
    if not (wording / "workers/checksums.json").is_file():
        from scripts.resume_wording_validation import generate as generate_workers
        announce("wording_workers", generate_workers(word_args))
    if not (wording / "checksums.json").is_file():
        announce("wording_preflight", run_wording_v2.supervise(word_args, dry=True))
        announce("wording_supervisors", run_wording_v2.supervise(word_args))
    follow.require_frozen(wording)
    announce("analysis", analyze_seal_followups.run(ROOT / "build/seal/analysis"))
    follow.write_json(ROOT / "build/seal/studies_complete.json", {
        "blind": follow.require_frozen(blind), "retry": follow.require_frozen(retry_dir),
        "wording": follow.require_frozen(wording)})
    announce('studies_complete', {'inference_collection_finished': True})
    from scripts.reproduce_all import run as reproduce_all
    announce('all_manuscripts', reproduce_all())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--after-blind-summary", type=Path,
                        default=ROOT / "build/seal/blind_collection_summary.json")
    run(parser.parse_args())
