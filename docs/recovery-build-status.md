# Recovery implementation and research status

This records the extension built on 10 November 2026. The earlier `plan-audit.md` is a historical comparison with commit `48704aa`, before this implementation. Building software does not complete the prospective research study.

## Implemented software

- `src/budgeted_recovery.py` implements sequential acquisition, a development-fitted record ranker, full/random/chronological/obligation comparators, identity-bound signed records, a shared gate, separate resource accounts, and explicit replay outcomes. It is an in-process replay framework, not a production archive boundary.
- `src/recovery_attacks.py` implements omission plans, archive and integrity failure regimes, bounded exact omission enumeration, and shared-budget queue simulation. An exhausted search is unresolved. The compromised-signer marker is evaluator-only and cannot inform release decisions.
- `src/recovery_baselines.py` implements TF-IDF and a classifier for supplied frozen embeddings, complete fitted-state digests, development provenance checks, and measured human-review logs. No real human reviews or embedding vectors have been collected.
- `src/recovery_calibration.py` implements finite-family binomial lower-tail testing and Clopper-Pearson bounds under independent Bernoulli-unit assumptions. Risk and confidence parameters are separate, template groups cannot be repeated within a candidate, and no feasible candidate means no selection. This is an implementation, not a calibrated result on the nine inspected templates. Utility needs its own endpoint; zero unsafe release alone can reward holding everything.
- `src/recovery_population.py` and `scripts/prepare_recovery_study.py` implement constructed controls, structural ancestry, split audits, observed-comparator input validation and bounded exploratory sizing. The renamed templates are development extensions, not independent confirmatory holdouts. A schema for live-worker records is not a live-worker study.
- `scripts/run_recovery_replay.py` connects the software on the inspected development fixtures. Its learned supervisor is local TF-IDF, not a live language model. Outputs are diagnostic replay, not prospective safety evidence.
- `scripts/analyze_recovery_costs.py` reports collection statuses, prompt sizes, tokens, latency and conservative charges from the paid development pilot. It excludes safety and superiority metrics because the constructed controls have documented duplicates and oracle limitations.
- `scripts/measure_workflow_context.py` measures full-record context feasibility with pinned public traces and a local tokenizer. Local token counts and input-price estimates are not provider usage or actual spend.

## Executed collection

`experiments/recovery-development` contains the original pre-call manifest, source snapshots, signed catalog, requests, responses and ledger for 360 scheduled calls. There are 353 completed, six failed and one invalid call. Conservative charges total 2.07630164 Adaption credits against the authorized 20-credit development cap. The collector raised a Windows ledger-replacement error; the final 360 terminal call files and ledger reconcile, with no pending requests or repeated inference. `collection_deviation.json` records that event.

Nine permitted/refusal pairs and six mistake/violation pairs have duplicate task, proposal, claims and record-body observations. These are constructed labels, not observed worker intentions. Consequently the pilot is a collection and cost diagnostic, not an unsafe-release benchmark or a power dataset.

Full retrieval fits easily on the constructed catalog. No retrieval superiority claim follows from these records. The scientific review is preserved in `recovery-scientific-review.md`; the later software fixes do not repair or relabel the frozen pilot.

## Planning choices delegated by the author

The author asked the agent to select targets and locate a corpus. Provisional planning targets are a five-percentage-point reduction in unsafe-release rate and no more than a two-percentage-point loss of legitimate completion, with 95% confidence and 80% power. These are research design choices, not requirements supplied by an operational user, observed effects, or a sample-size justification. A sample size must still use measured independent-group variation and a declared comparison/test.

The next feasibility check uses natural public software-agent workflow traces against the published Qwen 3.5 397B context limit of 131,072 tokens, reserving 8,192 for output, leaving 122,880 input tokens. The prospective engineering go rule is a one-sided 95% lower bound of at least 10% for the fraction exceeding that input limit. Trace selection must be frozen before length measurement. A convenience sample supports a diagnostic only; a binomial bound does not create random sampling or independence. Passing context feasibility would authorize designing a later policy experiment, not establish recovery safety.

## Remaining empirical work

The full proposed study still needs an adequate independent population, fresh worker behavior and refusal observations, hidden isolated executable checks, a frozen acquisition/stopping policy, model-family holdouts, real review measurements if claimed, matched measured costs, and an effect-sized sealed closed-loop evaluation. No external preregistration, human review, production trust separation, scalable robustness certificate or emergent-collusion result is claimed. The final main manuscript remains the empirical audit of the original fixed-arm study.
