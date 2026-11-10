# Plan audit

Historical snapshot against `48704aa`, before the recovery development implementation. See [the current build status](recovery-build-status.md) for later software and collection work; the empirical-study gaps below are not all closed by that implementation.

Date: 10 November 2026. Repository: `G:/swarm-alignment-2026-sprint`, GitHub `ArjunCodess/SEAL`, HEAD `48704aae101d9a64aeba2a3adf2a4d604922e758`.

The request is a complete budgeted-oversight plan and a final paper of that plan: a prospective study that buys attestations, fetches, and re-signs under a credit limit, with a frozen acquisition policy and closed-loop endpoints. This audit scores that request against the frozen release. The release is Live Supervisor Evidence Arms on Fixed Local Fixtures: protocol `ADAPTION-LIVE-MAIN-v3`, 960 scheduled one-shot supervisor calls, 909 completed, 29 invalid, 22 errors. State observations are a fixed snapshot (`adaptive: false` in `src/live_fixtures.py`). No adaptive retrieval study is in `data/study`, `src`, or the report.

`docs/budgeted-recovery-protocol.md` is an unregistered draft written with this audit. It specifies the missing design. It does not register the design, run it, or supply results.

Status labels:

- **Frozen-met.** The release already satisfies the requirement.
- **Frozen-partial.** A related frozen artifact exists and does not satisfy the requirement.
- **Absent.** The release has no artifact for the requirement.
- **Draft-only.** The unregistered protocol specifies it. No registry id, no spend, no outcomes.

## Named commits

These three commits are the only commits after the fixed-arm release. Each one keeps a rebuild or a zip from writing onto frozen inputs. None of them adds a protocol, a policy, an episode, an endpoint, a sample-size rule, or a paper of budgeted recovery.

| Commit | Date (+0530) | Files | What the diff does |
|---|---|---|---|
| `42ca5b92f173299c333355e62b423cf646a27a1a` | 10 Nov 2026 17:50 | `scripts/reproduce.py`, `tests/test_reproducibility.py` | `reproduce()` rejects an output path that resolves to the repo root, sits inside `data/study`, or is an ancestor of `data/study`. |
| `2492ca060ffe5763d52e82d44cea564e40973101` | 10 Nov 2026 17:51 | `scripts/package_release.py`, `tests/test_reproducibility.py` | `make_archive()` rejects a destination, a `.zip.partial`, or a `.verification.json` that resolves to an input artifact. |
| `48704aae101d9a64aeba2a3adf2a4d604922e758` | 10 Nov 2026 18:00 | `README.md` | Two sentences document those two guards. |

`42ca5b9` runs before `materialize_study` and does not change scoring. `2492ca0` runs when a zip is packed and does not change the study. `48704aa` documents both. A reader who treats these commits as the budgeted plan has the reproduction safeguard and still has the 960 one-shot episodes.

## Predecessor files that are not the plan

Uncommitted, left by an interrupted edit, and not part of HEAD:

- `paper/live_body.tex`, `paper/live_appendix.tex`, `scripts/prepare_live_paper.py`, `tests/test_live_paper.py`. These reword the fixed-arm report as an empirical audit, add the endpoint-field appendix, and state that prospective budgeted evidence recovery was not conducted. They do not define an acquisition policy or a recovery experiment.
- `docs/literature-matrix.md`. A targeted comparison dated 10 November 2026. It is not a systematic review, not a protocol, and not a result. Its go/no-go is adopted by the draft: measure full-retrieval cost on development data before any selective-acquisition claim. A credit cap on the SEAL calls does not establish a constrained oversight budget.

No partial `docs/plan-audit.md` or `docs/budgeted-recovery-protocol.md` was present before this pass. This pass did not change `data/study`, `src`, `scripts`, `paper`, or git state.

## Requirement audit

| # | Requirement | Status | Evidence in the release | Named commits |
|---|---|---|---|---|
| 1 | Final paper of the budgeted study | Absent | `paper/main.tex` reports the fixed-arm audit. The uncommitted body says recovery was not executed. | No paper file in any of the three diffs. |
| 2 | Executed adaptive retrieval study | Absent | 960 calls, one decision each. `STATE_QUERY_DISCLOSURE` says the state arm exposes no adaptive tool query. | No episode or `src` change. |
| 3 | Separate prospective registration | Absent | Frozen names are `ADAPTION-LIVE-MAIN-v3` (manifest sha256 `610eae252674a7b06f60d79d702b9e8a0cd80c3fb64edc8f51c607bc9f79a680`) and `ADAPTION-LIVE-PILOT-v3`. No second registry record. | No protocol file in the diffs. |
| 4 | Trust and observation model for priced recovery | Frozen-partial; draft-only for the loop | `supervisor_observation` hides harness labels. `execute_decision` lets claims, receipts, and state execute a publish, and lets provenance veto. Provenance still shows `trusted_summary` when `binding_complete` is true. | Guards do not change the observation. |
| 5 | Three task families and independent oracle controls | Frozen-partial | Four test families, three mechanisms. `consistent_fake_input` is repeated on `parts-catalog` and `sensor-calibration`. `reference_decision` is an equal-observation baseline, not a withheld oracle. | No catalog change. |
| 6 | Scenario and model holdouts for a frozen policy | Frozen-partial | Development families stay out of main denominators. Both `gemma-4-31b-it` and `qwen3.5-397b` are inside the 960 and both are reported. | No holdout rule. |
| 7 | Development pilot of realistic loop cost | Frozen-partial | Pilot ledger: 128 requests, 96 completed, 32 invalid, spent 0.48741652 of cap 5, `max_output_tokens` 2048. Main then pinned 8192 because of Qwen incompletes. The pilot is harness repair for one-shot arms. | No cost model. |
| 8 | Frozen acquisition policy versus random, chronological, rules, full, LM, and TF-IDF | Absent | Arms are `claims`, `receipts`, `state`, `provenance`. No ranker, no TF-IDF, no random fetch, no learned selector. | No policy code. |
| 9 | Adaptive concealment and workload attacks | Absent | Attacks are a fixed catalog. Worker ledger: 280/280 accepted calls, spent 0.01335031, and the report records byte-identical scripted wording. | No attack schedule. |
| 10 | Whole-trajectory risk calibration | Absent | `analysis_plan.json` (frozen 2026-11-08, main outcomes then uninspected) specifies a 2,000-replicate family-cluster bootstrap over four blocks. One decision per episode. | No analysis change. |
| 11 | Pilot-based sizing of the recovery study | Absent | Main N is the predeclared grid `40×2×4×2 + 40×1×4×2 = 960`, cap 20 credits, 960 requests. It was not sized from a fetch-loop pilot. | No sample-size rule. |
| 12 | Closed-loop primary endpoints at measured cost | Frozen-partial | Co-primary rates are one-shot unsafe execution and legitimate completion. Completed rows carry `cost_units`. Contrasts are not paired with the credits spent to acquire evidence, and budget-stops are not a policy action. | No endpoint change. |
| 13 | Actual resource requirements for the recovery loop | Frozen-partial | Fixed-arm prices, caps, and spends are recorded (table below). They price one supervisor call per arm, not a multi-buy loop. | No budget for a new study. |
| 14 | Standard partial-observation argument, with no new theorem | Frozen-partial; draft-only as an explicit argument | The report separates advice from the gate. It states no theorem. `docs/literature-matrix.md` lists the precedents and withholds a novelty claim. | README sentences do not argue recovery. |
| 15 | Rebuilds and release zips stay off frozen inputs | Frozen-met | `reproduce()` path check; `make_archive()` destination check; README lines added by `48704aa`. | This is the requirement those commits meet. |

## Notes on partial rows

**Trust model.** The frozen split is real and limited. Claims see the task, the candidate summary, and worker messages. Receipts add the signed packets and one fresh checkpoint. State adds keeper bodies and sets `state_query.adaptive` to false. Provenance adds trusted inputs, digests, the allowlist, and channels, and it is the only arm whose publish can be refused in code. On answerable attacks with complete binding, the model is shown the trusted summary. A recovery policy that is not to see the answer cannot reuse that bundle as its observation.

**Families and oracles.** Development mechanisms are `false_completion`, `wrong_summary`, `omitted_step`, and `stale_evidence` (four families, indexes 0–15). Test mechanisms are `consistent_fake_input`, `unauthorized_publication`, and `ambiguous_evidence` (four families, indexes 0–39). The generation manifest states that `stale_evidence` and `consistent_fake_input` share a digest mismatch rule across splits. Variants are `index // 4` and are not independent families. Honest controls always use three speakers; single versus coordinated changes message count and roles together. Those facts block any reading of the 960 as three independent oracle-controlled families.

**Pilot cost.** The pilot formula is `16 dev fixtures × 4 arms × 2 models = 128`. Spent total 0.48741652 credits under cap 5. That shows the one-shot harness can run on development families. It does not measure the cost of buying items until a budget stops, and it does not compare full retrieval with a selective policy.

**Endpoints and cost.** Rebuilt completed calls cost 3.409192 credits (Gemma 0.075222 over 448 calls, Qwen 3.333970 over 461). The main ledger spent 4.10737974 because invalid and failed calls are charged. Reporting those totals next to an execution rate is not a closed-loop, cost-matched contrast.

**Resources on record, fixed-arm only.**

| Item | Record |
|---|---|
| Models | `gemma-4-31b-it`, `qwen3.5-397b` |
| Historical prices per million | Gemma 0.09 input / 0.34 output; Qwen 0.55 / 3.5. Source pin: Adaption price page named in `data/study/main/budget.json` |
| Caps | Main 20 credits, 960 requests, output 8192, input 50,000 chars, reserve fraction 0.2, retries 0, no overage. Pilot cap 5, output 2048. Worker cap 2 |
| Spent | Main 4.10737974; pilot 0.48741652; workers 0.01335031 |
| Completed-call cost | Gemma mean 0.000168, p95 0.000244, max 0.000249. Qwen mean 0.007232, p95 0.013291, max 0.017761 |
| Credential | `ADAPTION_API_KEY` at collection time. Rebuild does not use it |

## Why the three commits are not the plan

The plan needs a new protocol, a frozen policy and six comparators, holdouts, a cost pilot, adaptive attacks, trajectory-level calibration, a size taken from that pilot, endpoints scored with the credits the loop actually spent, and a paper of that experiment. `42ca5b9` adds a path predicate so `build/` cannot cover `data/study`. `2492ca0` adds a path predicate so a zip cannot replace an input file. `48704aa` records both predicates in the README. The predicates protect the 960-episode artifact. They leave every scientific row above either partial or absent.

The draft protocol is the specification for rows 4–14. Its status is draft-only until a separate registration freezes a text, a spend cap, a pilot-derived size, and a policy hash, and until that registered study is run. The fixed-arm paper is not that final paper.
