# Natural workflow context feasibility

Full retrieval exceeded the configured input allowance on one of 30 sampled software-agent traces. The chosen context-feasibility go rule was not met. This is a diagnostic on one public archive and model stratum, not a safety study or a general result about agent workflows.

## Source and selection

The source is the third-party [SWE-bench Verified mini-swe-agent trajectory mirror](https://huggingface.co/datasets/tarsur385/swebench-verified-trajectories), revision `773748a7c1222e8a642a7059821498e14293562a`. Its pinned dataset card declares MIT and describes import from Docent. These are not a new SEAL collection. The card is not independent verification of the original run process. No standalone LICENSE file was present at that revision; the card and provenance records are retained.

The `claude-4.5-haiku-high` stratum lists 500 task directories. The 30 task IDs with smallest SHA-256 values of `swebench-verified-20261110:` followed by the task ID were selected. Integration reproduced this selection against all 500 directory names. A fixed hash avoids choosing cases by measured length, but the dataset, stratum and salt were research choices. The sample is not representative of all deployments. Different task IDs do not establish independence across related repository tasks.

`experiments/natural-workflow-costs/source_manifest.json` records selected paths, URLs, SHA-256 values, extraction rules, tokenizer revision and prompt. Raw traces and source records are retained. The delegate's initial Qwen 2.5 analysis is preserved under `provenance/preliminary-qwen2.5-audit.json` as superseded; its token counts and generic sample-size examples are not used in the final measurement.

## Measurement

The hosted model is `qwen3.5-397b`, whose metadata reports maximum input of 131,072 tokens. The configured planning allowance reserves 8,192 for output, leaving 122,880 input tokens. This reserve is a research configuration, not an observed operational SLA. The model metadata and public pricing page are retained under `provenance/`.

The local tokenizer is `Qwen/Qwen3.5-397B-A17B`, revision `8472618112abcbd45acbcdc58436aff4233c23f7`. Tokenizer files are hashed in the measurement freeze. Its chat template wraps an audit prompt containing JSON lines for archived message roles, content, tool calls and tool results. Run configuration and evaluation labels are excluded, and extraction avoids duplicating separate history and trajectory representations.

These are local tokenizer counts, not provider usage. No model inference was purchased for this check, and the input-price calculation is an estimate rather than billed spend. The sample was already inspected with another tokenizer, so this is a documented secondary measurement of the same fixed sample, not external preregistration.

| Quantity | Corrected local measurement |
|---|---:|
| Task groups / token counts resolved | 30 / 30 |
| Mean full-prompt tokens | 49,343.17 |
| Median full-prompt tokens | 43,441.5 |
| Minimum / maximum full-prompt tokens | 10,607 / 139,141 |
| Exceeding 122,880 tokens | 1 / 30 |
| One-sided 95% Clopper-Pearson lower bound | 0.0017083 |

The exceeding trace is `sympy__sympy-19040`. The chosen engineering rule required a lower bound of at least 0.10; the measured value does not meet it. The binomial bound assumes independent Bernoulli sampling and does not establish those assumptions for this archive. Failing the rule does not show that long workflows never need selective retrieval; this sample does not justify proceeding under the chosen rule.

## Reproduction

Install optional tokenizer dependencies and download only the pinned tokenizer files, then reproduce the measurement and chart:

```bash
python -m pip install -e ".[dev,context]"
python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen3.5-397B-A17B', revision='8472618112abcbd45acbcdc58436aff4233c23f7', allow_patterns=['tokenizer.json','tokenizer_config.json','vocab.json','merges.txt','config.json'], local_dir='build/tokenizers/qwen3.5')"
python scripts/measure_workflow_context.py --manifest experiments/natural-workflow-costs/source_manifest.json --output-dir build/natural-context
python scripts/plot_workflow_context.py --measurement build/natural-context/measurement.json --output docs/assets/natural-context-lengths.png
```

The retained `measurement/` directory contains the freeze, individual counts, summary, CSV and caveats. The five-percentage-point safety improvement and two-point completion-loss targets remain provisional design choices; these traces supply no malicious-coordination labels or measured recovery-policy effects for sizing that study.
