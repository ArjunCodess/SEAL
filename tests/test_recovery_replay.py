"""End-to-end offline tests for scripts/run_recovery_replay.py."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RecoveryReplayIntegrationTests(unittest.TestCase):
    def test_end_to_end_replay_in_tempdir(self) -> None:
        from scripts import run_recovery_replay as replay

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "replay_out"
            report = replay.main(["all", "--output-dir", str(out), "--seed", "20261110"])
            self.assertEqual(report, 0)

            paths = replay.paths_for(out)
            self.assertTrue(paths["manifest"].is_file())
            self.assertTrue(paths["trajectories"].is_file())
            self.assertTrue(paths["summary_csv"].is_file())
            self.assertTrue(paths["results_md"].is_file())

            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["replay_label"], replay.REPLAY_LABEL)
            self.assertEqual(manifest["seed"], 20261110)

            rows = [
                json.loads(line)
                for line in paths["trajectories"].read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("kind") != "omission_audit"
            ]
            completed = [r for r in rows if r.get("status") != "skipped"]
            self.assertGreater(len(completed), 0)
            for row in completed:
                self.assertTrue(row.get("sink_before_score"))
                self.assertIn("sink_outcome", row)
                self.assertIsNotNone(row["sink_outcome"])
                blob = json.dumps(row)
                for token in ("legitimate_oracle", "oracle_details"):
                    self.assertNotIn(f'"{token}"', blob)
                self.assertTrue(row.get("supervisor_observation_audit_ok"))

            from src import budgeted_recovery as br
            from src import recovery_attacks as ra
            from src import recovery_fixtures as fx

            sample = fx.build_cases(20261110)[0]
            evidence, archive, _meta = replay.build_evidence_bundle(
                sample,
                fx=fx,
                br=br,
                ra=ra,
            )
            observation = br.policy_observation(evidence, ())
            ra.audit_supervisor_observation(observation)
            obs_blob = json.dumps(observation)
            for token in ("legitimate", "control", "oracle", "scenario_group", "case_id"):
                self.assertNotIn(token, obs_blob)

            summary_a = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
            summary_b = json.loads(paths["summary_json"].read_text(encoding="utf-8"))
            self.assertEqual(summary_a, summary_b)

            csv_lines = paths["summary_csv"].read_text(encoding="utf-8").strip().splitlines()
            self.assertGreaterEqual(len(csv_lines), 2)
            header = csv_lines[0]
            self.assertNotIn("elapsed", header)

    def test_immutable_output_dir_refuses_mismatched_seed(self) -> None:
        from scripts import run_recovery_replay as replay

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "replay_out"
            replay.prepare_replay(out, seed=20261110)
            with self.assertRaises(replay.ResumeError):
                replay.prepare_replay(out, seed=20261111)


if __name__ == "__main__":
    unittest.main()
