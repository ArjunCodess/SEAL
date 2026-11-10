# Recovery pilot cost report (offline)

**Pilot label:** `SEAL-BUDGETED-RECOVERY-DRAFT-2026-11-10`

Cost-only aggregation. No unsafe-release rates, power estimates, or superiority claims.

## Scientific caveats

- Development-only catalog; not confirmatory and no safety or superiority claim.
- Record bodies are a few hundred bytes each; full retrieval is not an operational constraint on this catalog.
- The manifest spend cap is a collection reservation ceiling, not an oversight-policy budget.
- Do not report unsafe-release rates, power, or pooled model headlines from this pilot.
- Selective-acquisition superiority requires an external go rule and natural workflow archives.

## Collection status

- Scheduled episodes: 360
- Status histogram (scheduled set): `{'completed': 353, 'failed': 6, 'invalid': 1, 'scheduled': 360}`

## Duplicate observation flags

- Permitted/refusal duplicate scenario groups: 9
- Mistake/violation duplicate scenario groups: 6

## Consistency

- `scheduled_episodes`: 360
- `call_files_on_disk`: 360
- `extra_call_ids`: []
- `missing_or_pending_scheduled`: []
- `pending_ledger_episode_ids`: []
- `ledger_spent_total`: 2.076301639999998
- `ledger_request_count`: 360
- `sum_charged_from_calls_scheduled`: 2.07630164
- `sum_charged_from_ledger_episodes`: 2.07630164
- `ledger_vs_call_charge_delta`: 0.0
- `all_scheduled_calls_accounted`: True
- `request_pins_verified`: True

## Model / policy costs

### gemma-4-31b-it · chronological2 — statuses `{'completed': 36, 'scheduled': 36}`; usage credits 0.00297983; charged conservative 0.00297983; latency p50/p95 747.7602499966451/961.3896000046225 (n=36)
### gemma-4-31b-it · claims — statuses `{'completed': 36, 'scheduled': 36}`; usage credits 0.00249165; charged conservative 0.00249165; latency p50/p95 703.1057499989402/1505.8884249992843 (n=36)
### gemma-4-31b-it · full — statuses `{'completed': 35, 'invalid': 1, 'scheduled': 36}`; usage credits 0.00343471; charged conservative 0.00704496; latency p50/p95 811.1483000029693/1108.3691099971472 (n=35)
### gemma-4-31b-it · obligation2 — statuses `{'completed': 36, 'scheduled': 36}`; usage credits 0.00298515; charged conservative 0.00298515; latency p50/p95 737.5603500004217/915.1259000009304 (n=36)
### gemma-4-31b-it · random2 — statuses `{'completed': 36, 'scheduled': 36}`; usage credits 0.00293215; charged conservative 0.00293215; latency p50/p95 725.8102000014333/1521.4558749976277 (n=36)
### qwen3.5-397b · chronological2 — statuses `{'completed': 36, 'scheduled': 36}`; usage credits 0.40384080; charged conservative 0.40384080; latency p50/p95 23730.735049997747/46715.10195000155 (n=36)
### qwen3.5-397b · claims — statuses `{'completed': 34, 'failed': 2, 'scheduled': 36}`; usage credits 0.36857010; charged conservative 0.43684260; latency p50/p95 24042.606450002495/44043.51072999778 (n=34)
### qwen3.5-397b · full — statuses `{'completed': 35, 'failed': 1, 'scheduled': 36}`; usage credits 0.33246655; charged conservative 0.36716050; latency p50/p95 20406.94159999839/39574.9625700002 (n=35)
### qwen3.5-397b · obligation2 — statuses `{'completed': 35, 'failed': 1, 'scheduled': 36}`; usage credits 0.41970285; charged conservative 0.45384130; latency p50/p95 29547.017500000948/48290.97173000264 (n=35)
### qwen3.5-397b · random2 — statuses `{'completed': 34, 'failed': 2, 'scheduled': 36}`; usage credits 0.32788160; charged conservative 0.39618270; latency p50/p95 22638.150250000763/37304.99974500016 (n=34)
