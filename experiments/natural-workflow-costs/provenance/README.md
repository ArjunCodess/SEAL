---
license: mit
language:
- en
tags:
- swe-bench
- agent-trajectories
size_categories:
- 1K<n<10K
---

# SWE-bench Verified — mini-swe-agent trajectories (10 models)

Native `.traj.json` files from running mini-swe-agent on all 500 SWE-bench Verified instances
for each of the 10 models below. Downloaded from
[Docent](https://docent.transluce.org) via `preprocessing/swebench_docent.py` in
[contrastive_learning](https://github.com/jackyk02/contrastive_learning).

## Layout

```
swebench_verified_raw/
  <model_label>/
    <instance_id>/
      <instance_id>.traj.json
```

## Per-file schema

```python
{
  "instance_id": str,
  "trajectory_format": "mini-swe-agent-1.1",
  "messages": list[dict],
  "info": {
    "resolved": bool,
    "submission": str,
    "exit_status": str,
    "config": dict, "mini_version": str,
    "model_stats": dict,
    "scores": dict,
    "docent": {"model_label": str, "agent_run_id": str, "transcript_id": str}
  }
}
```

## Models (10)

- `claude-4.5-opus-high`
- `gemini-3-flash-high`
- `minimax-m2.5-high`
- `claude-opus-4.6`
- `gpt-5.2-codex`
- `gpt-5.2-high`
- `glm-5-high`
- `claude-4.5-haiku-high`
- `gpt-5-mini`
- `gemini-3-pro`

## Companion

Pre-computed Qwen3.6-27B tail-truncated trajectory embeddings:
`tarsur385/swebench-verified-trajectory-embeddings-qwen3.6-27b`.
