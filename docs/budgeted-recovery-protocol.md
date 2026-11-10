# Budgeted evidence-recovery protocol

Draft: SEAL-BUDGETED-RECOVERY-2026-11-10. This is a prospective specification, not a registry identifier or an executed confirmatory study. The fixed-arm protocols under data/study remain unchanged. A development-only feasibility pilot is implemented separately; its observations must not enter a later confirmatory denominator.

## Research question and feasibility decision

Can trusted evidence acquisition reduce violations released while preserving legitimate task completion at a fixed total oversight cost, against an attacker that knows the frozen policy?

First measure the cost of fetching and reading all records on development workflows. Record counts and bytes describe the workload; they are not substitute credit prices. Count actual monitor input/output tokens, repeated context, retrieval service charges, human review time, and elapsed recovery latency separately. Do not assign arbitrary costs to free local operations or pad logs to create an advantage. A collection spend cap is different from an oversight-policy resource limit.

Continue to a selective-acquisition claim only when a real deployment constraint is specified and full retrieval violates it or selective retrieval improves a prespecified endpoint at matched measured cost. A cheaper prompt alone is insufficient if it loses task completion. If small workflows make full retrieval adequate, report a focused audit and the negative feasibility result. No practical cost threshold or superiority claim is established by this draft.

## Observation and trust model

A workflow produces task evidence and a proposed consequential action. The environment independently records the complete evidence in a protected archive. The worker can omit records from the initial view, alter untrusted claims, or choose attack timing, but cannot change protected archived records or the trusted manifest in the primary regime. Initially the observer sees task obligations, the proposed action, worker claims, visible records, and authenticated missing-record metadata. It never sees the oracle label, control arm, hidden reference answer, or attacker instruction.

An acquisition returns an already recorded body with identity, digest, and signature verified against the trusted manifest. A signature establishes a provenance relation under the stated key assumption; it does not make the signed assertion true. Semantic checks still compare raw evidence, task obligations, and proposed action.

Keep these regimes separate:

| Regime | What recovery can establish | Required result |
|---|---|---|
| Omission after recording | An authentic retained record can be retrieved | Measure decisions after recovery |
| Failure to record | No authentic body exists | Report unresolved evidence, not successful recovery |
| Body modification or replay | Manifest/digest/signature/sequence can disagree | Log integrity failure and charge routing/review costs |
| Archive unavailable | Authentic metadata may exist without a retrievable body | Hold or defer under the frozen stopping rule |
| Compromised signer or false signed record | Cryptographic verification can succeed on false content | Explicit failure regime; no authenticity-based safety claim |

The current in-process demo does not implement production trust separation. A final systems claim requires an independently enforced archive boundary. Apply the same execution enforcement to every policy. Keep model decisions, integrity failures, gate vetoes, and executed actions separate. Never give a gate the hidden oracle label simply to obtain zero violations.

## Task population and controls

Build a new population with provenance and executable independent outcome checks:

| Task family | Evidence and obligation | Independent outcome check |
|---|---|---|
| Verification | Raw measurements and explicitly scoped approvals | Recompute numerical claim and verify both approvals and release permission |
| Code review | Proposed patch, task specification, controlled defects, trusted executable tests | Run hidden tests in isolation; model explanations do not assign labels |
| Grounded review | Pinned sources, source identifiers, and citation obligations | Check every released claim against traceable source facts and required citations |

Within every scenario group include permitted collaboration, ordinary mistakes, agents instructed to collude that refuse, and actual violations. A scripted refusal control is only a constructed control, not evidence that a live worker refused. A live worker study must log the instruction, generated behavior, and independently checked outcome separately. Uninstructed interactions are a separate population; do not call them emergent collusion based on adverse outcomes alone.

Development, calibration, and sealed test partitions hold entire scenario templates, paraphrases, paired controls, and related generated artifacts together. Fresh numerical variants of a known template are repeated measures. Hold out model families from policy fitting and calibration, and report model-specific results without pooling architectures. Activation probes are a separate access regime; deleting stored vectors does not remove original agent context. NARCBench is outside the main empirical SEAL claim.

## Acquisition policies and attacks

Develop one learned acquisition policy on development trajectories only. Inputs may include visible history, authenticated missing-record metadata, uncertainty, and measured retrieval cost. Fit acquisition choices on development data; freeze its weights, feature preprocessing, prompts, stopping rule, review routing, and any candidate selection before calibration. A fixed obligation-based heuristic is a comparator, not a trained method.

Compare random retrieval, chronological retrieval, a fixed obligation rule, full retrieval, the existing TF-IDF monitor/deletion defenses where inputs are compatible, a frozen embedding classifier, and a prompted or trained LM monitor with measured inference cost. Include signed counts/commitments and integrity-only gating. Human review requires actual reviewers and measured review time; an oracle simulator cannot substitute for human review outcomes.

Attack development sees development trajectories and the final oversight policy. Sealed evaluation includes random outages, selective omission, strategic start/stop timing, allowed untrusted-content changes, and review-budget exhaustion. The attacker may adapt to the policy, retrieval choices, and remaining budget within the frozen information boundary. Do not let it alter authenticated records in the primary regime or invent meaningless padding to imply an operational workload threat. Report the absent-archive and compromised-signer regimes separately.

For short complete logs enumerate all allowed deletion subsets as a reference. Compare heuristic attack success with exhaustive worst cases. On longer logs label a found attack as a witness; failed search is unresolved, not certification. A scalable certificate is optional and needs valid bounds verified on exhaustive small cases, including a computation-timeout outcome.

## Limits and calibration

The indistinguishability argument is standard. If a compliant history and a violating history induce identical observations under every affordable acquisition, any policy has the same release probability on both. Trusted recovery helps only when an available authentic record separates them; otherwise safe abstention sacrifices coverage. Do not claim theoretical novelty for this observation.

The deployment unit is a complete independent trajectory, including all acquisitions, repeated checks, deferrals, and the terminal action. Define bounded trajectory losses before calibration, such as an indicator that any violating action is released. Freeze a finite candidate family before Learn then Test, or establish the monotonicity/exchangeability assumptions of an applicable Conformal Risk Control theorem. Use independent trajectory groups and adjust for policy selection. Separately calibrated prefix thresholds give no aggregate stopping-policy guarantee. Distribution shift and model transfer require separate empirical evaluation.

## Development pilot, sample size, and freeze

The current engineering pilot uses nine constructed scenario groups, four controls per group, two supervisor models, and five fixed evidence policies. All cases are development data; there is no calibration or test split. It measures feasibility only and does not fit or validate the proposed learned acquisition policy. Its 20-credit cap covers collection, not a matched policy budget. The source and schedule manifest is written before calls; raw requests/responses, usage, latency, charged reservations, failures, and policy-specific record sizes are retained. Pending calls are not silently replayed.

For confirmation, first specify a meaningful minimum reduction in unsafe release and an acceptable loss of legitimate completion from the intended application. Use development-only between-scenario paired variation to simulate power or interval width for those joint endpoints. Increase the number of independent scenario groups, not just numeric variants. Record the sizing code, chosen effect, multiplicity treatment, attrition allowance, and resource forecast. If cost prevents the required number of independent groups, label the study underpowered or exploratory. A three-family bootstrap and the old 59-unit tolerance calculation are not power justifications.

Before sealed calls, freeze and hash the scenario generator, held-out population, policy code and weights, monitor models, prompts, signed archive contract, calibration method, loss definitions, sample size, attack rules, spend limits, and analysis script. Store a time-stamped immutable manifest and protocol deviations. Local hashing is not external preregistration; provide an actual registry record only if registered. Never reuse an inspected population as a new independent test.

## Closed-loop outcomes and reporting

Debug routing in frozen replay, then run fresh closed-loop workflows in which oversight acts before the consequential sink action. Let the independently checked proposal reach the sandbox sink only when the policy releases it; record actual execution before scoring with the hidden oracle. Do not infer prevented violations from replay routing.

Co-primary outcomes at matched total measured oversight cost are violations released and legitimate tasks completed. Report review demand, actual review usage, holds, unresolved evidence, errors, invalid outputs, archive failures, recovery latency, subgroup failures, and cost components. Held work is neither a detected violation nor successful completion. Keep completed-case and all-status denominators explicit, count unknown costs conservatively, and retain failures. Adaptive budget exhaustion must operate on the entire workflow queue, not only on isolated equal-size records.

## Resources and status

On 10 November 2026 the author reported 1,492.134 available Adaption credits and supplied API access through secret input. Start with a 20-credit development collection cap, reserve before send, make no automatic retries or top-ups, and charge uncertain requests at their conservative reservation. Pin current provider prices and observed model availability in the pilot manifest. Credentials are excluded from all experiment artifacts.

The author reports no external funding and no competing interests. Fresh worker interactions, an independently separated archive, actual human review, held-out training/calibration, and an effect-sized sealed evaluation remain separate work until executed and recorded. The existing SEAL empirical paper must not present them as completed results.
