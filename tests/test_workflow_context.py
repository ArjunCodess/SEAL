"""Tests for offline workflow context measurement (no paid API)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WhitespaceTokenizer:
  """Deterministic test tokenizer: one token per whitespace-delimited word."""

  def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
    _ = add_special_tokens
    if not text.strip():
      return []
    return list(range(1, len(text.split()) + 1))


class WorkflowContextTests(unittest.TestCase):
  def setUp(self) -> None:
    from scripts import measure_workflow_context as mwc

    self.mwc = mwc
    self._tmp = tempfile.TemporaryDirectory()
    self.base = Path(self._tmp.name)
    self.traces = self.base / "traces"
    self.traces.mkdir()
    self.out = self.base / "out"
    self.out.mkdir()

  def tearDown(self) -> None:
    self._tmp.cleanup()

  def _write_trace(self, name: str, payload: object) -> tuple[str, int]:
    path = self.traces / name
    text = json.dumps(payload, sort_keys=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    digest = self.mwc.content_sha256(data)
    return digest, len(data)

  def _manifest(
      self,
      records: list[dict],
      *,
      include_tokenizer: bool = False,
      fullprompt: str = "PROMPT\n{transcript}",
  ) -> Path:
    doc: dict = {
      "model_id": "test-qwen-pin",
      "base_dir": str(self.traces),
      "sample_rule": {"dedupe_groups": "min_workflow_id"},
      "fullprompt": fullprompt,
      "records": records,
    }
    if include_tokenizer:
      doc["tokenizer"] = {
        "hf_model": "Qwen/Test-Missing",
        "hf_revision": "deadbeef",
      }
    manifest = self.base / "manifest.json"
    manifest.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return manifest

  def test_injected_tokenizer_bytes_vs_tokens_and_outputs(self) -> None:
    body = {"messages": [{"role": "user", "content": "alpha beta gamma"}]}
    digest, _ = self._write_trace("a.json", body)
    manifest = self._manifest(
        [
            {
                "path": "a.json",
                "sha256": digest,
                "sourceURL": "https://example.test/a",
                "workflow_id": "wf-a",
                "group_id": "g1",
                "license": "MIT",
            }
        ]
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out, tokenizer=WhitespaceTokenizer())
    self.assertEqual(summary["resolved_token_count"], 1)
    measurement = json.loads((self.out / "measurement.json").read_text(encoding="utf-8"))
    row = measurement["records"][0]
    self.assertGreater(row["transcript_utf8_bytes"], 0)
    self.assertNotEqual(row["transcript_utf8_bytes"], row["input_tokens"])
    self.assertEqual(row["input_tokens"], 4)  # alpha beta gamma + transcript wrapper words in prompt
    self.assertTrue((self.out / "measurement.csv").is_file())
    md = (self.out / "results.md").read_text(encoding="utf-8")
    self.assertIn("local pinned tokenizer", md.lower())

  def test_protected_output_dir_rejects_scripts_tree(self) -> None:
    manifest = self._manifest([])
    bad_out = ROOT / "scripts" / "_ctx_report"
    with self.assertRaises(ValueError):
      self.mwc.measure_workflow_context(manifest, bad_out, tokenizer=WhitespaceTokenizer())

  def test_immutable_source_hash_mismatch_fails_unit(self) -> None:
    self._write_trace("b.json", {"text": "hello"})
    manifest = self._manifest(
        [
            {
                "path": "b.json",
                "sha256": "0" * 64,
                "sourceURL": "https://example.test/b",
                "workflow_id": "wf-b",
                "group_id": "g-hash",
                "license": "MIT",
            }
        ]
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out, tokenizer=WhitespaceTokenizer())
    group = summary["go_rule"]
    self.assertEqual(group["independent_group_denominator"], 1)
    self.assertEqual(group["exceedance_successes"], 0)
    record = json.loads((self.out / "measurement.json").read_text(encoding="utf-8"))["records"][0]
    self.assertEqual(record["status"], "failed")

  def test_duplicate_groups_pick_min_workflow_id(self) -> None:
    d1, _ = self._write_trace("w1.json", {"content": "one"})
    d2, _ = self._write_trace("w2.json", {"content": "two"})
    manifest = self._manifest(
        [
            {
                "path": "w2.json",
                "sha256": d2,
                "sourceURL": "https://example.test/w2",
                "workflow_id": "wf-z",
                "group_id": "dup",
                "license": "MIT",
            },
            {
                "path": "w1.json",
                "sha256": d1,
                "sourceURL": "https://example.test/w1",
                "workflow_id": "wf-a",
                "group_id": "dup",
                "license": "MIT",
            },
        ]
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out, tokenizer=WhitespaceTokenizer())
    self.assertEqual(summary["independent_group_count"], 1)
    self.assertEqual(len(summary["duplicate_group_audit"]), 1)
    self.assertEqual(summary["duplicate_group_audit"][0]["selected_workflow_id"], "wf-a")

  def test_failures_retained_in_denominator(self) -> None:
    d_ok, _ = self._write_trace("ok.json", {"content": "x" * 4000})
    manifest = self._manifest(
        [
            {
                "path": "ok.json",
                "sha256": d_ok,
                "sourceURL": "https://example.test/ok",
                "workflow_id": "wf-ok",
                "group_id": "g-ok",
                "license": "MIT",
            },
            {
                "path": "missing.json",
                "sha256": "a" * 64,
                "sourceURL": "https://example.test/missing",
                "workflow_id": "wf-miss",
                "group_id": "g-miss",
                "license": "MIT",
            },
        ],
        fullprompt="{transcript}",
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out, tokenizer=WhitespaceTokenizer())
    self.assertEqual(summary["go_rule"]["independent_group_denominator"], 2)
    self.assertEqual(summary["go_rule"]["exceedance_successes"], 0)

  def test_tokenizer_unavailable_yields_unresolved_not_go(self) -> None:
    d_ok, _ = self._write_trace("only.json", {"content": "abc"})
    manifest = self._manifest(
        [
            {
                "path": "only.json",
                "sha256": d_ok,
                "sourceURL": "https://example.test/only",
                "workflow_id": "wf-only",
                "group_id": "g-only",
                "license": "MIT",
            }
        ],
        include_tokenizer=True,
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out)
    self.assertEqual(summary["go_rule"]["decision"], "unresolved")
    self.assertFalse(summary["go_rule"]["go"])

  def test_cp_lower_bound_go_rule(self) -> None:
    long_text = "token " * 130_000
    digest, _ = self._write_trace("long.json", {"content": long_text})
    manifest = self._manifest(
        [
            {
                "path": "long.json",
                "sha256": digest,
                "sourceURL": "https://example.test/long",
                "workflow_id": f"wf-{i}",
                "group_id": f"g-{i}",
                "license": "MIT",
            }
            for i in range(10)
        ],
        fullprompt="{transcript}",
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out, tokenizer=WhitespaceTokenizer())
    bound = summary["go_rule"]["exceedance_fraction_lower_bound"]
    self.assertIsNotNone(bound)
    self.assertEqual(summary["go_rule"]["exceedance_successes"], 10)
    self.assertGreaterEqual(bound, self.mwc.GO_PI)
    self.assertTrue(summary["go_rule"]["go"])
    self.assertEqual(summary["go_rule"]["decision"], "go")

  def test_clopper_pearson_lower_bound_toy(self) -> None:
    lb = self.mwc.clopper_pearson_lower_bound(successes=3, n=20, delta=0.05)
    self.assertGreater(lb, 0.0)
    self.assertLess(lb, 3 / 20)
    self.assertEqual(self.mwc.clopper_pearson_lower_bound(successes=0, n=10, delta=0.05), 0.0)

  def test_jsonl_extraction(self) -> None:
    path = self.traces / "run.jsonl"
    lines = [
        json.dumps({"text": "line-one"}),
        json.dumps({"message": "line-two"}),
    ]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    path.write_bytes(data)
    digest = self.mwc.content_sha256(data)
    manifest = self._manifest(
        [
            {
                "path": "run.jsonl",
                "sha256": digest,
                "sourceURL": "https://example.test/jsonl",
                "workflow_id": "wf-jsonl",
                "group_id": "g-jsonl",
                "license": "Apache-2.0",
            }
        ],
        fullprompt="{transcript}",
    )
    summary = self.mwc.measure_workflow_context(manifest, self.out, tokenizer=WhitespaceTokenizer())
    self.assertEqual(summary["resolved_token_count"], 1)


if __name__ == "__main__":
  unittest.main()
