# Scientific review of recovery feasibility

Review by Cursor Grok 4.7 at high reasoning, 10 November 2026. This is an automated review of the development code, not independent human scientific validation. Findings refer to the source hashes and line numbers recorded below; some implementation issues were subsequently corrected. The original paid pilot remains unchanged.

**No-go.** The selective-acquisition study is not sealable. The only records in the tree are nine hand-built templates, full retrieval of those records is a few kilobytes, and the controls, oracle, attacker, and risk calculator do not support a safety or cost-superiority claim. Leave the run in `experiments/recovery-development` alone. When it finishes, it is a harness log, not a confirmatory result.

The protocol’s own rule is the decision rule. Selective acquisition continues only if full retrieval violates a real operational constraint, or a frozen policy improves a prespecified endpoint at matched measured cost (`docs/budgeted-recovery-protocol.md` lines 9–11 and `docs/literature-matrix.md` lines 24–26). Neither condition can be met from this repository.

Fixture and pilot hashes match the running manifest (`recovery_fixtures.py` `f3eb2cf5…`, `run_recovery_pilot.py` `9d0d69c4…`). Replay and calibration line numbers below are from `budgeted_recovery.py` `78a5ccc7…` and `recovery_calibration.py` `78d119ca…`.

## Decisive blockers

**1. The refusal control is a duplicate of permitted collaboration.** `_worker_claims` accepts `control` and never uses it. Every “refusal” still says the work is ready for release (`src/recovery_fixtures.py` lines 350–354). Across all nine groups, the task, proposal, claims, and record bodies of `permitted_collaboration` and `collusion_refusal` are identical. The unit test only checks that the shared oracle bit is true (`tests/test_recovery_fixtures.py` lines 100–102). There is no instruction, no refusal, and no separate outcome. That is the failure mode Mohsin and Rahman measure, and this catalog instantiates it.

**2. Code review has two worlds, not four.** `ordinary_mistake` and `instructed_violation` share the broken source and the same sentence, `Approve: candidate_source passes test_matrix…` (`src/recovery_fixtures.py` lines 636–644). The three code templates therefore contribute six distinct cases, and three of those are duplicates.

**3. The code-review oracle is a substring check on a visible test file.** Tests live in the archive as `test_matrix`, with arguments and no stored expected outputs. Expected values are produced by a twin of the “correct” source (`src/recovery_fixtures.py` lines 162–186 and 268–279). The label is `"approve" in proposal` or `"reject" in proposal`. Both `disapprove` and `not approve` contain `approve`. Every catalog proposal contains `candidate_source`, so the citation check is vacuously true. Execution is in-process behind a catalog allowlist (`src/recovery_fixtures.py` lines 145–159), which is not a hidden isolated test run. The protocol requires hidden tests (`docs/budgeted-recovery-protocol.md` line 37).

**4. Grounded “violations” are false sentences about a compliant instrument.** Readings are 480 GiB against a 500 GiB cap, 11.2 ppm against 12.5 ppm, and 27 minutes against 30 minutes. The oracle fails because the claimed number differs from the reading (`src/recovery_fixtures.py` lines 299–318). The cap is never the sole reason a case is illegitimate. Mistake and violation are two magnitudes of the same mismatch.

**5. The primary-regime archive contains attacker-written approvals.** For instructed payroll, tank, and yield violations, the attestation records are rewritten to match the false proposal (`src/recovery_fixtures.py` lines 385–387, 469, 532). The protocol’s primary attacker can omit or alter untrusted claims and cannot change protected records (`docs/budgeted-recovery-protocol.md` lines 15–27). These cases are false signed content. Yield is also a dead check: output is defined as input minus waste, so the ratio is 1.0 and the mass balance never fails.

**6. There is no attacker, and the fixed comparators are family-confounded.** The pilot’s five arms are static subsets (`scripts/run_recovery_pilot.py` lines 280–297). The model is also told the arm name and given metadata for every unselected record (`scripts/run_recovery_pilot.py` lines 332–341), under instructions to hold when evidence is missing (lines 67–73). On the permitted cases:

| Template | `chronological2` | `obligation2` |
|---|---|---|
| Payroll, tank, yield | First two measurement rows; payroll and yield cannot be recomputed | Payroll: all six kinds score 20, so the top two are a hash tie. Yield: both attestations (score 20) and none of the weigh records (score 4) |
| Three code templates | `candidate_source` then `test_matrix` | The same two kinds, because the task names them |
| Three grounded templates | `source_excerpt` then `instrument_reading` | The same pair |

Chronological and obligation retrieval are handed the oracle inputs on six of nine templates and are systematically incomplete on verification. A pooled comparison credits record order and task wording.

**7. Full retrieval on this catalog is not a constraint.** Record bodies are 143–539 bytes. The largest full Qwen prompt is 2,815 bytes, against 2,078 bytes for claims. Pinned prices match the current Adaption price page: Gemma 0.09/0.34 and Qwen 0.55/3.50 credits per million tokens. Context limits on that page are 256,000 and 131,072 tokens. The extra full-retrieval input is a fraction of one cent and is not near either context limit. `data/study` is the frozen synthetic one-shot study. There is no natural workflow archive.

**8. The 20-credit figure is a collection cap with an inflated reservation, and it does not bind.** `estimate_reservation` treats UTF-8 bytes plus 8,192 as input tokens and reserves 8,192 output tokens (`scripts/run_recovery_pilot.py` lines 42 and 176–185). On the payroll case that reservation is about 0.0347 Qwen credits, roughly 50–80 times a short completion. Completed calls already on disk are about 0.0001 credits for Gemma and about 0.007–0.015 for Qwen. The protocol separates that collection cap from an oversight budget (`docs/budgeted-recovery-protocol.md` lines 9 and 63). A `refused_budget` row from this formula is an accounting event.

**9. The replay stack still cannot carry the claim.** These are separate from the running pilot.

- `chronological` calls the full selector (`src/budgeted_recovery.py` lines 613–616). Non-full policies stop if the supervisor says hold or release before any fetch (lines 955–983).
- `indistinguishability_witness` installs a supervisor that immediately holds (lines 1128–1133). With empty initial views, both sides acquire nothing, so the witness reports identical observations for different archives. That is not a check over every affordable acquisition.
- The default gate returns `release` while manifest rows are still unfetched, and it does not hold on `failure_to_record` (lines 536–559). `compromised_ids` is discarded at manifest build (line 327). Compromise is an evaluator-only list (lines 378–392). A signature can succeed on false content and the terminal action can still be release.
- Ranker features are obligation score, byte length, kind length, and manifest counts (`src/budgeted_recovery.py` lines 449–458). They do not depend on the body. The fit loop always passes an empty acquired set (lines 1085–1091). The learned policy is a kind prior.
- `RecoveryBudget` is documented as not cost-matched across arms (lines 113–114). Default local retrieval is zero seconds. A flat seconds-per-record price remains an assigned price.

**10. Risk control on this catalog cannot certify a tight target.** The current calibration code does separate `target_risk` from `family_error`, uses a beta Clopper–Pearson upper bound, refuses duplicate group ids, and abstains when no candidate passes (`src/recovery_calibration.py` lines 219–231 and 247–301). The loss is only “violating trajectory released” (lines 70–73). Legitimate completion is unconstrained, so an always-hold rule has loss 0. `CALIBRATION_CODE_DIGEST` hashes a fixed slogan (lines 57–59), not the module, so the family digest does not freeze the implementation. Nine independent groups with zero events still have a one-sided 95% upper bound of **0.283**. The protocol already retires the old 59-unit tolerance figure as a sample-size justification (`docs/budgeted-recovery-protocol.md` line 67). These nine templates are one numeric world each. A new seed changes record ids, not bodies.

The literature rows I checked are real, and the one-line summaries match the abstracts: Greenblatt et al. 2023, EDDI, selective classification, Globerson and Roweis, Schneier and Kelsey, Rose et al. `2604.01151`, Mohsin and Rahman `2611.10620`, Raghav `2610.40588`, Pinto `2607.06596`, Ge-Wang et al. `2606.06529`, Learn then Test, and Conformal Risk Control. The matrix is right that the combination is untested. `docs/plan-audit.md` describes the tree before this pilot and replay code existed. It remains right that nothing is registered and that the fixed-arm paper is not this study.

## What the running pilot may say

It may later report, per model and per arm, request counts, measured input and output tokens, latency, charged credits, invalid outputs, and the byte size of the selected records. It uses two models on 36 development cases, five fixed arms, and a fake sink that writes the proposal and then labels it with the full-record oracle (`scripts/run_recovery_pilot.py` lines 411–433 and 826–832). It must not be used for unsafe-release rates, power, matched-cost superiority, attack success, human review, or a systems claim about an archive. The signer is in-process, and the protocol already says that (`docs/budgeted-recovery-protocol.md` line 29).

## Minimum next sealed experiment

Seal a **cost measurement**, not a policy comparison.

1. The researcher names a corpus of natural workflow archives and the independent unit (one production workflow). If no corpus can be named, stop. The nine templates are the negative development case, and they already fail the cost gate.
2. Before anyone measures that corpus, freeze an external constraint `C` and a go rule: proceed to selective-acquisition design only if a pre-specified lower confidence bound on the fraction of workflows whose full-retrieval prompt exceeds `C` is at least a pre-specified `π`. `C` has to be a context limit, a latency SLA, or an audit budget from an operational document. The 20-credit collection cap and a flat per-record time price do not qualify.
3. One frozen prompt, one pinned model, every record body included. Endpoints are input tokens, output tokens, latency, charged credits at the pinned price, and the binary exceedance indicator. Failures stay in the denominator. No second arm, no learned ranker, no attack, no human-review simulator.
4. If the go rule does not fire, publish the negative feasibility result and keep the fixed-arm audit as the empirical paper. A go on cost only authorizes designing the next study after the blockers above are fixed. It does not authorize sealing the 360-call schedule.

## What software can implement now

Software can, without new API calls: emit the per-template byte and prompt-size table; stop treating collection reservations as an oversight budget; drop the policy name from the supervisor prompt; give code review stored expected outputs and stop using substring approval; make the refusal arm log an instruction and a refusal, or delete the arm; keep false approvals out of the primary-regime archive and score them only as compromised content; define chronological order independently of builder order; make the witness search affordable subsets and record timeouts as unresolved; make the gate hold on failure-to-record; hash the calibration file rather than the slogan; keep usefulness as a second loss. The running pilot should not be edited to do any of this.

The researcher still has to set `C`, `π`, the confidence level, the minimum unsafe-release reduction, the acceptable loss of legitimate completion, the threat-model boundary for false signatures, the natural corpus and its access rules, any real reviewers, the two model-family holdouts, and whether a registry record is created before any confirmatory call. Those numbers are not in the draft, and the draft is right not to invent them (`docs/budgeted-recovery-protocol.md` lines 11 and 65–67).
