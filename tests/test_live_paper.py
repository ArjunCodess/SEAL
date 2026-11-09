import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_live_paper.py"
BUILD_TMP = ROOT / "build" / "tmp"
WORD_RE = re.compile(r"\b[\w]+(?:[-'][\w]+)*\b")
TEST_VARIANTS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
TEST_FAMILIES = [
    "parts-catalog",
    "payroll-release",
    "incident-timeline",
    "sensor-calibration",
]
WORKER_CORPUS_SHA256 = "f8c79dd758e05fd5cfc8a36cc965762b27d77db20aa7fcbbcd6f6c768cfbdf7e"


def scratch_dir() -> Path:
    path = BUILD_TMP / "pytest_live_paper"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_worker_dir() -> Path | None:
    primary = ROOT / "data" / "study" / "workers"
    if (primary / "assembled_corpus.json").is_file():
        return primary
    return None


def tectonic_available() -> bool:
    if shutil.which("tectonic"):
        return True
    for candidate in (
        ROOT / ".tools" / "tectonic" / "tectonic.exe",
        ROOT / ".tools" / "tectonic" / "tectonic",
        ROOT / ".tools" / "tectonic.exe",
    ):
        if candidate.is_file():
            return True
    return False


def run_prepare(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPT), *args]
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True)


def build_synthetic_main_bundle(
    root: Path,
    *,
    models: list[str] | None = None,
    bootstrap_replicates: int = 2000,
    seed: int = 20261108,
    include_protocol_source_hashes: bool = False,
) -> tuple[Path, Path]:
    """Full-grid main-ready study and analysis (960 episodes, 120 fixtures)."""
    from src.live_analysis import ARMS, run_analysis

    models = models or ["gemma-4-31b-it", "qwen3.5-397b"]

    def episode(model: str, arm: str, coordination: str, family_id: str, variant: int, legitimate: bool) -> dict:
        fixture_id = f"{family_id}.{variant}.{coordination}"
        unsafe_execution = (not legitimate) and arm == "claims" and coordination == "coordinated"
        unsafe_attempt = (not legitimate) and coordination != "honest" and arm in ("claims", "receipts")
        publish_executed = (legitimate or arm == "claims") and coordination != "honest"
        return {
            "episode_id": f"{model}-{arm}-{coordination}-{fixture_id}",
            "model": model,
            "arm": arm,
            "fixture_id": fixture_id,
            "family_id": family_id,
            "split": "test",
            "coordination": coordination,
            "mechanism": "honest" if legitimate else "consistent_fake_input",
            "legitimate": legitimate,
            "answerable": True,
            "status": "completed",
            "decision": {
                "action": "publish" if publish_executed else "block",
                "summary": {"note": "synthetic"},
                "reason": "synthetic",
            },
            "outcome": {
                "publication_attempted": publish_executed or unsafe_attempt,
                "publication_executed": publish_executed,
                "unsafe_attempt": unsafe_attempt,
                "unsafe_execution": unsafe_execution,
                "legitimate_completion": legitimate and publish_executed,
            },
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "latency_seconds": 0.1,
            "cost_units": 0.01,
        }

    records: list[dict] = []
    schedule_fixture_ids: list[str] = []
    for family_id in TEST_FAMILIES:
        for variant in TEST_VARIANTS:
            for coordination in ("single", "coordinated", "honest"):
                fixture_id = f"{family_id}.{variant}.{coordination}"
                schedule_fixture_ids.append(fixture_id)
                for model in models:
                    for arm in ARMS:
                        records.append(
                            episode(
                                model,
                                arm,
                                coordination,
                                family_id,
                                variant,
                                legitimate=coordination == "honest",
                            )
                        )

    study = root / "study"
    analysis = root / "analysis"
    study.mkdir(parents=True, exist_ok=True)
    episodes = root / "episodes.jsonl"
    episodes.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf8")
    run_analysis(episodes, analysis, bootstrap_replicates=bootstrap_replicates, seed=seed)
    manifest = {
        "mode": "main",
        "protocol_name": "ADAPTION-LIVE-MAIN-v3",
        "models": models,
        "schedule_fixture_ids": schedule_fixture_ids,
        "worker_corpus_sha256": WORKER_CORPUS_SHA256,
    }
    (study / "run_manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf8")
    protocol_doc: dict = {
        "protocol_name": "ADAPTION-LIVE-MAIN-v3",
        "schedules": {"main": {"target_episodes": 960}},
    }
    if include_protocol_source_hashes:
        main_protocol = ROOT / "data" / "study" / "main" / "protocol.json"
        if main_protocol.is_file():
            protocol_doc["source_hashes"] = json.loads(main_protocol.read_text(encoding="utf8")).get(
                "source_hashes"
            ) or {}
    (study / "protocol.json").write_text(json.dumps(protocol_doc) + "\n", encoding="utf8")
    (study / "ledger.json").write_text(json.dumps({"episodes": {
        row["episode_id"]: {"status": row["status"]} for row in records
    }}) + "\n", encoding="utf8")
    (study / "episodes.jsonl").write_text(episodes.read_text(encoding="utf8"), encoding="utf8")
    analysis_summary = json.loads((analysis / "summary.json").read_text(encoding="utf8"))
    analysis_summary.setdefault("input", {})["path"] = str(study / "episodes.jsonl")
    analysis_summary["input"]["sha256"] = hashlib.sha256((study / "episodes.jsonl").read_bytes()).hexdigest()
    (analysis / "summary.json").write_text(json.dumps(analysis_summary, indent=2) + "\n", encoding="utf8")
    return study, analysis


class LivePaperGeneratorTests(unittest.TestCase):
    def test_temp_fixture_writes_qa_and_refuses_final_claims(self):
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            out = Path(temporary) / "qa"
            proc = run_prepare(["--temp-fixture", "--output-dir", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            for name in (
                "generated.tex",
                "abstract.tex",
                "claim_to_evidence.json",
                "build_status.json",
                "live_co_primary.png",
                "live_trust_boundary.png",
                "table_main_results.tex",
                "results_narrative.tex",
                "conclusion.tex",
            ):
                path = out / name
                self.assertTrue(path.is_file(), name)
                self.assertGreater(path.stat().st_size, 0, name)
            self.assertTrue((out / "figures" / "live_trust_boundary.png").is_file())
            status = json.loads((out / "build_status.json").read_text(encoding="utf8"))
            self.assertEqual(status["render_mode"], "temp_fixture")
            self.assertFalse(status["main_results_ready"])
            claims = json.loads((out / "claim_to_evidence.json").read_text(encoding="utf8"))
            self.assertFalse(claims["main_results_ready"])
            abstract = (out / "abstract.tex").read_text(encoding="utf8")
            words = WORD_RE.findall(abstract)
            self.assertGreaterEqual(len(words), 150)
            self.assertLessEqual(len(words), 250)
            self.assertNotIn("Aggregate rows exported", abstract)

    def test_protocol_draft_without_main_analysis(self):
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            out = Path(temporary) / "qa"
            proc = run_prepare(["--output-dir", str(out)])
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            status = json.loads((out / "build_status.json").read_text(encoding="utf8"))
            self.assertEqual(status["render_mode"], "protocol_draft")
            self.assertFalse(status["main_results_ready"])
            narrative = (out / "results_narrative.tex").read_text(encoding="utf8")
            self.assertIn("withheld", narrative.lower())
            self.assertNotIn("Until then", narrative)

    def test_force_final_exits_without_main(self):
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            out = Path(temporary) / "qa"
            proc = run_prepare(
                ["--temp-fixture", "--output-dir", str(out), "--force-final"],
            )
            self.assertNotEqual(proc.returncode, 0)
            proc2 = run_prepare(["--output-dir", str(out), "--force-final"])
            self.assertNotEqual(proc2.returncode, 0)

    def test_final_mode_from_synthetic_analysis(self):
        worker_dir = resolve_worker_dir()
        if worker_dir is None:
            self.skipTest("worker corpus not available under data/study/workers")
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            root = Path(temporary)
            study, analysis = build_synthetic_main_bundle(root, include_protocol_source_hashes=True)
            out = root / "qa"
            compile_args = ["--compile"] if tectonic_available() else []
            proc = run_prepare(
                [
                    "--study-dir",
                    str(study),
                    "--analysis-dir",
                    str(analysis),
                    "--worker-dir",
                    str(worker_dir),
                    "--output-dir",
                    str(out),
                    *compile_args,
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            status = json.loads((out / "build_status.json").read_text(encoding="utf8"))
            self.assertEqual(status["render_mode"], "final")
            self.assertTrue(status["main_results_ready"])
            abstract = (out / "abstract.tex").read_text(encoding="utf8")
            words = WORD_RE.findall(abstract)
            self.assertGreaterEqual(len(words), 180, abstract)
            self.assertLessEqual(len(words), 250, abstract)
            self.assertNotIn("pooled:", abstract.lower())
            self.assertIn("not pooled", abstract.lower())
            self.assertNotIn("strongest", abstract.lower())
            self.assertNotIn("often publish", abstract.lower())
            generated = (out / "generated.tex").read_text(encoding="utf8")
            self.assertIn("ADAPTION-LIVE-MAIN-v3", generated)
            self.assertIn("\\def\\WorkerVerbatimIdentical{280}", generated)
            aggregate_rows = len(
                [line for line in (analysis / "aggregate.csv").read_text().splitlines()[1:] if line.strip()]
            )
            self.assertEqual(aggregate_rows, 24)
            summary = json.loads((analysis / "summary.json").read_text(encoding="utf8"))
            self.assertEqual(summary["input"]["episodes"], 960)
            self.assertEqual(summary["analysis"]["bootstrap"]["replicates"], 2000)
            table = (out / "table_aggregate.tex").read_text(encoding="utf8")
            self.assertIn("Gateway block", table)
            self.assertIn("honest", table)
            narrative = (out / "results_narrative.tex").read_text(encoding="utf8")
            self.assertIn("table_paired_focus", narrative)
            self.assertIn("of 4 families", abstract)
            self.assertIn("identical completed fixture sets", abstract)
            release_figure = ROOT / "figures" / "live_co_primary.png"
            release_before = release_figure.stat().st_mtime if release_figure.is_file() else None
            paper_pdf = ROOT / "paper" / "main.pdf"
            paper_before = paper_pdf.stat().st_mtime if paper_pdf.is_file() else None
            if compile_args:
                self.assertTrue((out / "main.pdf").is_file())
                if paper_before is not None and paper_pdf.is_file():
                    self.assertEqual(paper_pdf.stat().st_mtime, paper_before)
            if release_before is not None and release_figure.is_file():
                self.assertEqual(release_figure.stat().st_mtime, release_before)

    def test_rejects_truncated_grid_bootstrap(self):
        worker_dir = resolve_worker_dir()
        if worker_dir is None:
            self.skipTest("worker corpus not available")
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            root = Path(temporary)
            study, analysis = build_synthetic_main_bundle(root, bootstrap_replicates=40)
            out = root / "qa"
            proc = run_prepare(
                [
                    "--study-dir",
                    str(study),
                    "--analysis-dir",
                    str(analysis),
                    "--worker-dir",
                    str(worker_dir),
                    "--output-dir",
                    str(out),
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            status = json.loads((out / "build_status.json").read_text(encoding="utf8"))
            self.assertFalse(status["main_results_ready"])
            generated = (out / "generated.tex").read_text(encoding="utf8")
            self.assertIn("bootstrap", generated.lower())

    def test_co_primary_figure_destination_not_double_live_prefix(self):
        worker_dir = resolve_worker_dir()
        if worker_dir is None:
            self.skipTest("worker corpus not available")
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            root = Path(temporary)
            study, analysis = build_synthetic_main_bundle(root, include_protocol_source_hashes=True)
            out = root / "qa"
            stale = out / "figures" / "live_live_co_primary.png"
            final_figure = out / "figures" / "live_co_primary.png"
            proc = run_prepare(
                [
                    "--study-dir",
                    str(study),
                    "--analysis-dir",
                    str(analysis),
                    "--worker-dir",
                    str(worker_dir),
                    "--output-dir",
                    str(out),
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            self.assertTrue(final_figure.is_file())
            self.assertFalse(stale.is_file())
            self.assertGreater(final_figure.stat().st_size, 50_000)
            source_size = (analysis / "live_co_primary.png").stat().st_size
            self.assertEqual(final_figure.stat().st_size, source_size)

    def test_zero_denominator_stratum_in_final_abstract(self):
        import pandas as pd

        worker_dir = resolve_worker_dir()
        if worker_dir is None:
            self.skipTest("worker corpus not available")
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            root = Path(temporary)
            study, analysis = build_synthetic_main_bundle(root, include_protocol_source_hashes=True)
            agg_path = analysis / "aggregate.csv"
            frame = pd.read_csv(agg_path)
            mask = (frame["arm"] == "claims") & (frame["coordination"] == "coordinated")
            frame.loc[mask, "illegitimate_completed"] = 0
            frame.loc[mask, "illegitimate_unsafe_execution_rate"] = float("nan")
            frame.loc[mask, "illegitimate_outcome_unsafe_executions"] = float("nan")
            frame.to_csv(agg_path, index=False)
            out = root / "qa"
            proc = run_prepare(
                [
                    "--study-dir",
                    str(study),
                    "--analysis-dir",
                    str(analysis),
                    "--worker-dir",
                    str(worker_dir),
                    "--output-dir",
                    str(out),
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            abstract = (out / "abstract.tex").read_text(encoding="utf8")
            self.assertIn("not estimable", abstract)

    def test_worker_counts_from_live_manifest(self):
        worker_dir = resolve_worker_dir()
        if worker_dir is None:
            self.skipTest("worker corpus not available")
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            out = Path(temporary) / "qa"
            proc = run_prepare(
                ["--output-dir", str(out), "--worker-dir", str(worker_dir)],
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            generated = (out / "generated.tex").read_text(encoding="utf8")
            self.assertIn("\\def\\WorkerFixtureSchedule{120}", generated)
            self.assertIn("\\def\\WorkerMessagesSchedule{280}", generated)
            self.assertIn("\\def\\WorkerAccepted{280}", generated)
            self.assertIn("\\def\\WorkerGeneratedFixtures{120}", generated)
            self.assertIn("\\def\\WorkerScriptedFallbackFixtures{0}", generated)
            claims = json.loads((out / "claim_to_evidence.json").read_text(encoding="utf8"))
            self.assertEqual(claims["worker_corpus"]["fixtures_scheduled"], 120)
            self.assertTrue(claims["worker_corpus"]["corpus_hash_ok"])

    @unittest.skipUnless(tectonic_available(), "no Tectonic compiler on PATH or under .tools")
    def test_compile_temp_fixture_pdf_isolated_from_paper_sources(self):
        paper_pdf = ROOT / "paper" / "main.pdf"
        paper_pdf_before = paper_pdf.stat().st_mtime if paper_pdf.is_file() else None
        release_fig = ROOT / "figures" / "live_co_primary.png"
        release_fig_before = release_fig.stat().st_mtime if release_fig.is_file() else None
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            out = Path(temporary) / "qa"
            proc = run_prepare(["--temp-fixture", "--output-dir", str(out), "--compile"])
            output = (proc.stderr or "") + (proc.stdout or "")
            self.assertEqual(proc.returncode, 0, output)
            self.assertTrue((out / "main.pdf").is_file())
            banner = (out / "status_banner.tex").read_text(encoding="utf8")
            self.assertIn("TEMP FIXTURE WATERMARK", banner)
            if paper_pdf_before is not None and paper_pdf.is_file():
                self.assertEqual(paper_pdf.stat().st_mtime, paper_pdf_before)
            elif paper_pdf.is_file():
                self.fail("compile must not write paper/main.pdf")
            if release_fig_before is not None and release_fig.is_file():
                self.assertEqual(release_fig.stat().st_mtime, release_fig_before)

    def test_portable_temp_fixture_from_copied_minimal_checkout(self):
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            checkout = Path(temporary) / "checkout"
            for relative in ("scripts", "src", "paper"):
                shutil.copytree(ROOT / relative, checkout / relative, ignore=shutil.ignore_patterns("live_generated", "*.pdf"))
            out = checkout / "build" / "paper"
            proc = run_prepare(["--temp-fixture", "--output-dir", str(out)], cwd=checkout)
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            status = json.loads((out / "build_status.json").read_text(encoding="utf8"))
            self.assertEqual(status["render_mode"], "temp_fixture")

    def test_main_readiness_rejects_incomplete_episode_grid(self):
        worker_dir = resolve_worker_dir()
        if worker_dir is None:
            self.skipTest("worker corpus not available")
        with tempfile.TemporaryDirectory(dir=scratch_dir()) as temporary:
            root = Path(temporary)
            study, analysis = build_synthetic_main_bundle(root, include_protocol_source_hashes=True)
            summary = json.loads((analysis / "summary.json").read_text(encoding="utf8"))
            summary["input"]["episodes"] = 959
            (analysis / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf8")
            out = root / "qa"
            proc = run_prepare(
                [
                    "--study-dir",
                    str(study),
                    "--analysis-dir",
                    str(analysis),
                    "--worker-dir",
                    str(worker_dir),
                    "--output-dir",
                    str(out),
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            status = json.loads((out / "build_status.json").read_text(encoding="utf8"))
            self.assertFalse(status["main_results_ready"])
            generated = (out / "generated.tex").read_text(encoding="utf8")
            self.assertIn("959", generated)


if __name__ == "__main__":
    unittest.main()
