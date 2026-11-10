"""Offline workflow-context measurement for natural agent traces (no API calls)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]

CONTEXT_WINDOW_TOKENS = 131_072
OUTPUT_RESERVE_TOKENS = 8_192
MAX_INPUT_TOKENS = CONTEXT_WINDOW_TOKENS - OUTPUT_RESERVE_TOKENS  # 122880, explicit

GO_PI = 0.10
GO_ONE_SIDED_CONFIDENCE = 0.95
GO_ALPHA = 1.0 - GO_ONE_SIDED_CONFIDENCE

INPUT_CREDITS_PER_MILLION = 0.55  # pinned Qwen input list price; estimate only

SCIENTIFIC_CAVEATS = [
    "Token counts use a locally pinned Hugging Face tokenizer (or test adapter), not provider billing tokenizers.",
    "Byte lengths are UTF-8 octets of the extracted transcript text; bytes are not substituted for tokens.",
    "Input credit estimates apply the pinned input price only; they are not measured API spend.",
    "The context go rule is cost-only and independent of recovery unsafe-release planning targets.",
    "Unknown token counts are treated as non-exceedance for the exceedance-fraction bound (conservative).",
    "Failed ingest units remain in the exceedance denominator.",
]

TEXT_FIELD_KEYS = frozenset(
    {
        "content",
        "text",
        "message",
        "observation",
        "action",
        "thought",
        "response",
        "command",
        "output",
        "reasoning",
        "arguments",
        "tool_calls",
    }
)


class WorkflowContextError(Exception):
    pass


class SupportsEncode(Protocol):
    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]: ...


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json_atomic(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def validate_output_dir(output_dir: Path) -> Path:
    root = output_dir.resolve()
    if root == ROOT or root in ROOT.parents:
        raise ValueError("Report output cannot replace the repository or its ancestors")
    for name in ("data", "src", "scripts", "tests", "paper", "docs", "experiments", ".git"):
        protected = (ROOT / name).resolve()
        if root == protected or root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError("Report output must be separate from protected source and input trees")
    return root


def clopper_pearson_lower_bound(*, successes: int, n: int, delta: float) -> float:
    """One-sided Clopper–Pearson lower confidence bound for binomial proportion."""

    if n <= 0:
        raise ValueError("n must be positive for Clopper–Pearson lower bound")
    if successes < 0 or successes > n:
        raise ValueError("successes must satisfy 0 <= successes <= n")
    if not (0.0 < delta < 1.0):
        raise ValueError("delta must be in (0,1)")
    if successes <= 0:
        return 0.0
    from scipy.stats import beta as scipy_beta

    return float(scipy_beta.ppf(delta, successes, n - successes + 1))


def tokenizer_spec_digest(spec: Mapping[str, Any] | None) -> str | None:
    if not spec:
        return None
    files = {}
    if spec.get("local_path"):
        directory = Path(str(spec["local_path"]))
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.name in {"tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt", "special_tokens_map.json", "config.json", "added_tokens.json"} and ".cache" not in path.parts:
                files[path.relative_to(directory).as_posix()] = content_sha256(path.read_bytes())
    payload = {"spec": dict(spec), "local_files_sha256": files}
    return content_sha256(canonical_json(payload).encode("utf-8"))


@dataclass
class HFChatTokenizer:
    """Apply the pinned model's chat framing to the full audit prompt."""
    tokenizer: Any

    def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
        return self.tokenizer.apply_chat_template(
            [{"role": "user", "content": text}], tokenize=True, add_generation_prompt=True, return_dict=False
        )


def load_hf_tokenizer(spec: Mapping[str, Any]) -> SupportsEncode:
    local_path = spec.get("local_path")
    hf_model = spec.get("hf_model")
    hf_revision = spec.get("hf_revision")
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise WorkflowContextError(
            "transformers is required for Hugging Face tokenizers; inject a tokenizer in tests"
        ) from exc
    if local_path:
        path = Path(str(local_path))
        if not path.is_dir():
            raise WorkflowContextError(f"tokenizer local_path is not a directory: {path}")
        for relative, expected in spec.get("files_sha256", {}).items():
            source = (path / relative).resolve()
            if not source.is_relative_to(path.resolve()) or content_sha256(source.read_bytes()) != expected:
                raise WorkflowContextError("Pinned tokenizer file hash mismatch")
        return HFChatTokenizer(AutoTokenizer.from_pretrained(
            str(path),
            local_files_only=True,
            revision=str(hf_revision) if hf_revision else None,
        ))
    if hf_model and hf_revision:
        return HFChatTokenizer(AutoTokenizer.from_pretrained(
            str(hf_model),
            revision=str(hf_revision),
            local_files_only=True,
        ))
    raise WorkflowContextError(
        "tokenizer spec requires local_path or both hf_model and hf_revision"
    )


def count_input_tokens(tokenizer: SupportsEncode, text: str) -> int:
    encoded = tokenizer.encode(text, add_special_tokens=True)
    return len(encoded)


def _collect_strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        stripped = node.strip()
        if stripped:
            out.append(node)
        return
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in TEXT_FIELD_KEYS:
                _collect_strings(value, out)
            elif isinstance(value, (Mapping, list)):
                _collect_strings(value, out)
        return
    if isinstance(node, list):
        for item in node:
            _collect_strings(item, out)


def _load_extraction_spec(path: Path) -> Mapping[str, Any]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise WorkflowContextError(f"extraction spec must be a JSON object: {path}")
    return doc


def extract_transcript_text(
    *,
    raw: bytes,
    suffix: str,
    extraction: Mapping[str, Any] | None,
    extraction_path: Path | None,
) -> str:
    spec: Mapping[str, Any] | None = extraction
    if extraction_path is not None:
        spec = _load_extraction_spec(extraction_path)

    if spec and str(spec.get("strategy")) == "utf8_whole_file":
        return raw.decode("utf-8")

    lower = suffix.lower()
    if lower == ".jsonl":
        parts: list[str] = []
        for line_no, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WorkflowContextError(f"invalid jsonl line {line_no}") from exc
            chunk: list[str] = []
            _collect_strings(row, chunk)
            if chunk:
                parts.append("\n".join(chunk))
            else:
                parts.append(canonical_json(row))
        return "\n".join(parts)

    if lower in {".json", ".traj"}:
        try:
            doc = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkflowContextError("invalid JSON trace") from exc
        if spec and spec.get("strategy") in {"trajectory_fields", "message_fields"}:
            default_root = "messages" if spec["strategy"] == "message_fields" else "trajectory"
            trajectory = doc[str(spec.get("root_key", default_root))]
            fields = spec.get("fields", ["role", "content", "tool_calls", "tool_call_id", "function", "error"] if default_root == "messages" else ["action", "observation"])
            return "\n".join(canonical_json({key: row[key] for key in fields if key in row}) for row in trajectory)
        if spec and str(spec.get("strategy")) == "json_canonical":
            return canonical_json(doc)
        chunk: list[str] = []
        _collect_strings(doc, chunk)
        if chunk:
            return "\n".join(chunk)
        return canonical_json(doc)

    if spec and str(spec.get("strategy")) == "utf8_whole_file":
        return raw.decode("utf-8")

    raise WorkflowContextError(f"unsupported trace format {suffix!r} without extraction spec")


def build_full_prompt(*, template: str, transcript: str) -> str:
    if "{transcript}" in template:
        return template.replace("{transcript}", transcript)
    return f"{template}{transcript}"


@dataclass(frozen=True, slots=True)
class RecordRow:
    relpath: str
    sha256: str
    source_url: str
    workflow_id: str
    group_id: str
    license: str


def _parse_manifest(doc: Mapping[str, Any], manifest_path: Path) -> tuple[dict[str, Any], list[RecordRow]]:
    records_raw = doc.get("records")
    if not isinstance(records_raw, list) or not records_raw:
        raise WorkflowContextError("manifest.records must be a non-empty array")

    base_dir = Path(str(doc.get("base_dir") or manifest_path.parent)).resolve()
    rows: list[RecordRow] = []
    for index, item in enumerate(records_raw):
        if not isinstance(item, dict):
            raise WorkflowContextError(f"records[{index}] must be an object")
        for key in ("path", "sha256", "sourceURL", "workflow_id", "group_id", "license"):
            if key not in item or not str(item[key]).strip():
                raise WorkflowContextError(f"records[{index}] missing required field {key!r}")
        relpath = str(item["path"])
        rows.append(
            RecordRow(
                relpath=relpath,
                sha256=str(item["sha256"]).lower(),
                source_url=str(item["sourceURL"]),
                workflow_id=str(item["workflow_id"]),
                group_id=str(item["group_id"]),
                license=str(item["license"]),
            )
        )
    return {"base_dir": base_dir, "doc": doc}, rows


def freeze_measurement_digest(
    *,
    manifest_sha256: str,
    doc: Mapping[str, Any],
    tokenizer_digest: str | None,
) -> str:
    payload = {
        "manifest_sha256": manifest_sha256,
        "measurement_source_sha256": content_sha256(Path(__file__).read_bytes()),
        "model_id": doc.get("model_id"),
        "tokenizer_spec_digest": tokenizer_digest,
        "sample_rule": doc.get("sample_rule"),
        "fullprompt": doc.get("fullprompt"),
        "context_constraint": doc.get(
            "context_constraint",
            {
                "context_tokens": CONTEXT_WINDOW_TOKENS,
                "output_reserve_tokens": OUTPUT_RESERVE_TOKENS,
                "max_input_tokens": MAX_INPUT_TOKENS,
            },
        ),
        "go_rule": doc.get(
            "go_rule",
            {"pi": GO_PI, "confidence": GO_ONE_SIDED_CONFIDENCE, "bound": "clopper_pearson_lower"},
        ),
    }
    return content_sha256(canonical_json(payload).encode("utf-8"))


def select_group_representatives(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    """One deterministic workflow per group_id (minimum workflow_id)."""

    by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        by_group[str(row["group_id"])].append(row)

    chosen: list[Mapping[str, Any]] = []
    audit: list[dict[str, Any]] = []
    for group_id in sorted(by_group.keys()):
        members = sorted(by_group[group_id], key=lambda r: str(r["workflow_id"]))
        pick = members[0]
        chosen.append(pick)
        if len(members) > 1:
            audit.append(
                {
                    "group_id": group_id,
                    "selected_workflow_id": pick["workflow_id"],
                    "dropped_workflow_ids": [m["workflow_id"] for m in members[1:]],
                }
            )
    return chosen, audit


def measure_workflow_context(
    manifest_path: Path,
    output_dir: Path,
    *,
    tokenizer: SupportsEncode | None = None,
    tokenizer_loader: Callable[[Mapping[str, Any]], SupportsEncode] | None = None,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    out_root = validate_output_dir(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = content_sha256(manifest_bytes)
    doc = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(doc, dict):
        raise WorkflowContextError("manifest must be a JSON object")

    meta, manifest_rows = _parse_manifest(doc, manifest_path)
    base_dir: Path = meta["base_dir"]
    sample_rule = doc.get("sample_rule") or {"dedupe_groups": "min_workflow_id"}
    fullprompt_template = doc.get("fullprompt")
    if not isinstance(fullprompt_template, str) or not fullprompt_template.strip():
        raise WorkflowContextError("manifest.fullprompt must be a non-empty string template")

    constraint = doc.get("context_constraint") or {}
    max_input = int(constraint.get("max_input_tokens", MAX_INPUT_TOKENS))
    if max_input != MAX_INPUT_TOKENS:
        raise WorkflowContextError(
            f"context max_input_tokens must equal explicit Qwen budget {MAX_INPUT_TOKENS}"
        )

    go_rule = doc.get("go_rule") or {}
    go_pi = float(go_rule.get("pi", GO_PI))
    go_conf = float(go_rule.get("confidence", GO_ONE_SIDED_CONFIDENCE))
    go_delta = 1.0 - go_conf

    tok_spec = doc.get("tokenizer")
    if not isinstance(tok_spec, dict):
        tok_spec = None
    tok_digest = tokenizer_spec_digest(tok_spec)
    freeze_digest = freeze_measurement_digest(
        manifest_sha256=manifest_sha256,
        doc=doc,
        tokenizer_digest=tok_digest,
    )

    if out_root == base_dir or out_root.is_relative_to(base_dir) or base_dir.is_relative_to(out_root):
        raise WorkflowContextError("Measurement output must not overlap trace inputs")
    freeze_path = out_root / "freeze.json"
    if freeze_path.exists():
        existing = json.loads(freeze_path.read_text(encoding="utf-8"))
        if existing["freeze_digest"] != freeze_digest:
            raise WorkflowContextError("Output contains a different frozen measurement")
    else:
        save_json_atomic(freeze_path, {"freeze_digest": freeze_digest, "manifest_sha256": manifest_sha256, "measurement_source_sha256": content_sha256(Path(__file__).read_bytes())})

    active_tokenizer: SupportsEncode | None = tokenizer
    tokenizer_status = "injected"
    if active_tokenizer is None:
        if tok_spec is None:
            tokenizer_status = "unavailable"
        else:
            try:
                loader = tokenizer_loader or load_hf_tokenizer
                active_tokenizer = loader(tok_spec)
                tokenizer_status = "loaded_local"
            except (WorkflowContextError, OSError, ValueError) as exc:
                tokenizer_status = f"unavailable:{type(exc).__name__}"
                active_tokenizer = None

    record_results: list[dict[str, Any]] = []
    records_raw = doc.get("records")
    assert isinstance(records_raw, list)

    for index, item in enumerate(records_raw):
        assert isinstance(item, dict)
        row = manifest_rows[index]
        path = (base_dir / row.relpath).resolve()
        status = "ok"
        error: str | None = None
        transcript = ""
        transcript_bytes = 0
        input_tokens: int | None = None
        full_prompt = ""
        full_prompt_bytes = 0
        exceeds: bool | None = None
        est_input_credits: float | None = None

        extraction_path: Path | None = None
        if item.get("text_extraction"):
            extraction_path = (base_dir / str(item["text_extraction"])).resolve()

        try:
            if not path.is_file():
                raise WorkflowContextError(f"missing trace file: {path}")
            raw = path.read_bytes()
            digest = content_sha256(raw)
            if digest != row.sha256:
                raise WorkflowContextError(
                    f"sha256 mismatch for {row.relpath}: expected {row.sha256}, got {digest}"
                )
            extraction = item.get("extraction") if isinstance(item.get("extraction"), dict) else None
            transcript = extract_transcript_text(
                raw=raw,
                suffix=path.suffix,
                extraction=extraction,
                extraction_path=extraction_path,
            )
            transcript_bytes = len(transcript.encode("utf-8"))
            full_prompt = build_full_prompt(template=fullprompt_template, transcript=transcript)
            full_prompt_bytes = len(full_prompt.encode("utf-8"))

            if active_tokenizer is None:
                input_tokens = None
                exceeds = None
            else:
                input_tokens = count_input_tokens(active_tokenizer, full_prompt)
                exceeds = bool(input_tokens > max_input)
                est_input_credits = (input_tokens * INPUT_CREDITS_PER_MILLION) / 1_000_000
        except WorkflowContextError as exc:
            status = "failed"
            error = str(exc)
            exceeds = False
        except OSError as exc:
            status = "failed"
            error = str(exc)
            exceeds = False

        record_results.append(
            {
                "path": row.relpath,
                "sha256": row.sha256,
                "sourceURL": row.source_url,
                "workflow_id": row.workflow_id,
                "group_id": row.group_id,
                "license": row.license,
                "status": status,
                "error": error,
                "transcript_utf8_bytes": transcript_bytes,
                "full_prompt_utf8_bytes": full_prompt_bytes,
                "input_tokens": input_tokens,
                "exceeds_max_input_tokens": exceeds,
                "estimated_input_credits": est_input_credits,
            }
        )

    group_units, dedupe_audit = select_group_representatives(record_results)

    resolved_token_rows = [u for u in group_units if u.get("input_tokens") is not None]
    all_unresolved = len(resolved_token_rows) == 0

    exceed_successes = sum(1 for u in group_units if u.get("exceeds_max_input_tokens") is True)
    group_n = len(group_units)
    cp_lower = (
        clopper_pearson_lower_bound(successes=exceed_successes, n=group_n, delta=go_delta)
        if group_n > 0
        else None
    )

    if all_unresolved:
        decision = "unresolved"
        go = False
    elif cp_lower is None:
        decision = "unresolved"
        go = False
    else:
        go = bool(cp_lower >= go_pi)
        decision = "go" if go else "no_go"

    summary: dict[str, Any] = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "freeze_digest": freeze_digest,
        "sample_rule": sample_rule,
        "tokenizer_status": tokenizer_status,
        "tokenizer_spec_digest": tok_digest,
        "context_constraint": {
            "context_tokens": int(constraint.get("context_tokens", CONTEXT_WINDOW_TOKENS)),
            "output_reserve_tokens": int(constraint.get("output_reserve_tokens", OUTPUT_RESERVE_TOKENS)),
            "max_input_tokens": max_input,
        },
        "go_rule": {
            "pi": go_pi,
            "confidence": go_conf,
            "bound": "clopper_pearson_lower",
            "exceedance_successes": exceed_successes,
            "independent_group_denominator": group_n,
            "exceedance_fraction_lower_bound": cp_lower,
            "decision": decision,
            "go": go,
        },
        "duplicate_group_audit": dedupe_audit,
        "record_count_manifest": len(record_results),
        "independent_group_count": group_n,
        "resolved_token_count": len(resolved_token_rows),
        "caveats": list(SCIENTIFIC_CAVEATS),
        "input_credits_per_million_estimate": INPUT_CREDITS_PER_MILLION,
    }

    measurement = {
        "summary": summary,
        "records": record_results,
        "group_units": group_units,
    }
    save_json_atomic(out_root / "measurement.json", measurement)

    csv_path = out_root / "measurement.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "group_id",
                "workflow_id",
                "path",
                "status",
                "transcript_utf8_bytes",
                "full_prompt_utf8_bytes",
                "input_tokens",
                "exceeds_max_input_tokens",
                "estimated_input_credits",
                "sha256",
                "sourceURL",
                "license",
            ],
        )
        writer.writeheader()
        for row in record_results:
            writer.writerow(
                {
                    "group_id": row["group_id"],
                    "workflow_id": row["workflow_id"],
                    "path": row["path"],
                    "status": row["status"],
                    "transcript_utf8_bytes": row["transcript_utf8_bytes"],
                    "full_prompt_utf8_bytes": row["full_prompt_utf8_bytes"],
                    "input_tokens": row["input_tokens"] if row["input_tokens"] is not None else "",
                    "exceeds_max_input_tokens": row["exceeds_max_input_tokens"],
                    "estimated_input_credits": row["estimated_input_credits"]
                    if row["estimated_input_credits"] is not None
                    else "",
                    "sha256": row["sha256"],
                    "sourceURL": row["sourceURL"],
                    "license": row["license"],
                }
            )

    md_lines = [
        "# Workflow context measurement",
        "",
        "Cost-only exceedance screen using a **local pinned tokenizer**, not provider usage.",
        "",
        "## Freeze",
        f"- Manifest SHA-256: `{manifest_sha256}`",
        f"- Freeze digest: `{freeze_digest}`",
        f"- Tokenizer spec digest: `{tok_digest}`",
        f"- Tokenizer status: `{tokenizer_status}`",
        "",
        "## Context constraint (Qwen pin)",
        f"- Context window: {CONTEXT_WINDOW_TOKENS} tokens",
        f"- Output reserve: {OUTPUT_RESERVE_TOKENS} tokens",
        f"- Max input tokens: **{max_input}**",
        "",
        "## Go rule",
        f"- Target lower bound on exceedance fraction: **{go_pi:.2f}**",
        f"- One-sided confidence: {go_conf:.2f}",
        f"- Exceedance successes / groups: {exceed_successes} / {group_n}",
        f"- Clopper–Pearson lower bound: {cp_lower}",
        f"- Decision: **{decision}** (go={go})",
        "",
        "## Cost estimate (input only)",
        f"- Pinned input price: {INPUT_CREDITS_PER_MILLION} credits / million tokens (not measured spend).",
        "",
        "## Caveats",
    ]
    md_lines.extend(f"- {line}" for line in SCIENTIFIC_CAVEATS)
    if dedupe_audit:
        md_lines.extend(["", "## Duplicate groups (deduped)", ""])
        for row in dedupe_audit:
            md_lines.append(
                f"- `{row['group_id']}` kept `{row['selected_workflow_id']}`; dropped {row['dropped_workflow_ids']}"
            )
    (out_root / "results.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure workflow transcript bytes and tokenizer input tokens (offline)"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="JSON manifest listing immutable trace files and tokenizer pin",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "build" / "workflow-context",
        help="Writable report directory",
    )
    args = parser.parse_args(argv)
    try:
        summary = measure_workflow_context(args.manifest, args.output_dir)
    except (WorkflowContextError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "decision": summary["go_rule"]["decision"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
