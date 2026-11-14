# SEAL execution plan for the four venues

Planning date: 13 November 2026. Inspected revision: `62397205a5f471652ca7d9c26e25746ce0c43d2c`. This is a prospective implementation plan, not a report of new experiments or an external preregistration. No inference calls were made to prepare it.

The project should establish how model publication requests differ from enforced outcomes, then measure decisions with the trusted answer hidden and after one gate response. The second study tests new worker text and adds the missing honest speaker control. More calls cannot turn four families into broad evidence about deployed agents.

## What exists and what must remain fixed

`python scripts/reproduce.py --verify-only` passed during planning: 26 frozen study files, five pinned sources, and the separate recovery artifacts verified. The original ledger has 960 requests and charges 4.10737974 credits. Its historical prices and balance are not current account evidence.

`src/live_analysis.py` already aggregates publish decisions, unsafe attempts, executions, and pairing inventory. It lacks a complete, equally presented decision-endpoint analysis and the requested compact coverage report. `scripts/reproduce.py` already audits candidate, true, and invalid summaries. That audit does not replace the five specified mutations.

Keep `data/study` byte-for-byte unchanged, including reference tables, source contracts, protocols, and hashes. Do not repair its 51 invalid/error episodes with new calls. All additions get separate manifests and generated tables. Keep existing recovery and natural-workflow diagnostics out of these study denominators.

The frozen contract pins all five core modules, including `live_analysis.py`, `live_study.py`, and `live_workers.py`. Before editing them, preserve the exact five source files in a versioned legacy source directory outside `data/study`, verified against the existing contract. Add an explicit legacy reproduction path that runs those sources in isolation, and an extension path with its own source manifest. Do not rewrite the original checksums or silently disable verification. An additive extension table must not be compared as though its schema were the original reference table.

## Required claims in every submitted version

- Claims, receipts, and the fixed snapshot still publish the completed attacks in the recorded catalog. Report exact per-cell counts rather than implying that receipts and state publish every case.
- Provenance execution zeros come from `_provenance_action` in `src/live_fixtures.py`, not learned refusal. Its equality check blocks when `trusted_summary != summary`; earlier state, binding, and authorization checks can also veto a request, including a corrected true summary on an illegitimate fixture.
- Saved provenance observations were shown the true summary when available. The blind extension must remain distinguishable from those observations.
- All 280 accepted original worker calls copied the template because `wording_reference` was in the prompt.
- Paired contrasts retain 2/4 families for each Gemma attack stratum, 0/4 for Qwen single, and 1/4 for Qwen coordinated. `incident-timeline` is unpaired in every attack stratum.
- Attacks are instructed, the sink is fake and local, `side_effects` is false, and signing keys are public demo keys.
- Do not claim learned refusal, general weakness of receipts, spontaneous collusion, or a causal coordination effect from three speakers.

## Implementation order and acceptance criteria

### 1. Expose both endpoints on the frozen calls

Extend `src/live_analysis.py` and its tests, with no new calls. Normalize `execute_decision` fields once at the boundary: raw `attempted_publication` is adapted to the analysis schema's `publication_attempted`. Use `unsafe_attempt` for unsafe decision requests and `unsafe_execution` for unsafe outcomes; cross-check publish actions on attack fixtures against attempted publication.

Generate `build/seal/analysis/two_endpoints.csv` with model, arm, coordination, attack scheduled/completed/invalid/error counts, decision numerator, execution numerator, and explicit denominators. Generate the corresponding TeX table from these rows. Include honest completion and unsafe alterations on honest fixtures as separate diagnostics so blanket blocking cannot look like useful supervision.

Keep completed-case rates and all-scheduled sensitivity distinct. An error is an unresolved decision, not evidence of refusal. Add worst-case missing-outcome bounds where useful, without changing the frozen primary analysis. Show counts alongside rates. Extend family-block uncertainty to the decision endpoint, preserving the existing seed and reporting that only four families are available. Do not pool models or treat repeated fixtures as independent families.

Acceptance: all 960 IDs and original status counts are unchanged; replay agrees with saved outcomes; claims/receipts/state decision and execution rates match on attacks; provenance requests and gate outcomes remain separate. Mark the new presentation and analyses as retrospective additions to the frozen protocol.

### 2. Run the gate mutation sweep

Add `scripts/mutate_gate.py` and `tests/test_gate_mutations.py`. Use the actual `execute_decision`, not a copied implementation. Enumerate frozen attack fixtures by fixture and coordination, keeping duplicate presentations distinct from independent mechanisms. Start from an explicitly recorded candidate summary and mutate exactly one summary field or key operation at a time: one numeric value, a swapped key, an empty summary, the oracle true summary, and an extra key. Fix numeric-field and key-selection rules before running the sweep. Record before/after summaries and hashes in a companion manifest.

Write the required `build/gate_mutations.csv` with fixture, mutation, gate action, and `unsafe_execution`. Add coordination and gate reason, or supply them in a companion detail table. A block and an abstention are different actions. Never pre-fill the result with expected zeros. The equality check is only one of several checks, and an earlier veto can hide its effect.

Add legitimate true-summary positive controls so a gate that blocks everything cannot pass the audit. Add an isolated summary-mismatch control on an otherwise valid fixture to test the equality check specifically. Report these controls separately from frozen attack mutations. If no attack mutation publishes, say exactly that; if one publishes, retain it and investigate before freezing any paper.

Acceptance: every specified fixture/mutation pair appears once; deterministic reruns match; tests exercise block, abstain where applicable, and legitimate publication; the paper reports observed results and reasons. A mutation audit is catalog coverage, not a proof of general gate security.

### 3. Publish pairing coverage

Extend `src/live_analysis.py` to generate `build/seal/analysis/pairing_coverage.csv` with model, coordination, endpoint, families eligible, families paired, and reason unpaired. Add per-family rows identifying missing completed arms or unequal completed fixture sets. Preserve the original all-four-arm matching rule; do not silently replace it with an easier pairwise rule.

Acceptance: attack coverage reproduces Gemma 2/4 in both strata, Qwen 0/4 single and 1/4 coordinated; `incident-timeline` is explicitly excluded in every attack stratum. A zero-family contrast is unavailable, not a zero effect. Marginal counts remain visible alongside matched-subset contrasts.

### 4. Collect and freeze blind provenance

Proposed runner: `scripts/run_blind_provenance.py`, with protocol and analysis specification committed before collection. Use the same 40 fixtures, original worker corpus, both supervisors, and provenance observation only. Set the outward observation's `trusted_summary` to null; leave the fixture and executable gate unchanged. Test that the prompt contains neither that trusted answer nor hidden oracle labels through another field. Retain all other evidence, even if it permits the model to infer the answer.

The exact schedule is 40 fixtures x three conditions x two models = 240 calls: 160 attack calls and 80 existing three-speaker honest controls. Freeze prompt templates, model IDs, generation settings, schedule order/seed, output limits, parser, and error policy first. Availability changes require a recorded amendment, never a silent model substitution.

Write only to `data/study_blind_provenance`, with protocol, source/prompt hashes, denominator, reservation ledger, raw request/response records, normalized episodes, and checksums. A frozen run is terminal, reconciled, hashed, and reproducible; unresolved pending requests prevent freezing. Invalids and errors remain in the ledger. No outcome-dependent replacements.

Generate `build/seal/analysis/provenance_comparison.csv` reporting old decisions, old executions, blind decisions, and blind executions per model and condition. Report honest completion separately. For old-versus-blind decision contrasts, show common completed fixture IDs and exclusions as well as marginal scheduled/completed counts. The historical comparison changes answer visibility but is collected at a different time, so do not claim it perfectly isolates a causal effect if backend versions are unpinned.

Acceptance: 240 scheduled IDs reconcile; the observation-only nulling test passes; the gate implementation hash matches the original; all four rates regenerate from saved calls; original study hashes are unchanged.

### 5. Give blocked blinded attacks one retry

Start only after stage 4 is frozen. Proposed runner: `scripts/run_gate_retry.py`. Eligible parents are completed blinded attack calls that requested publication and received a summary block from the gate. Pin the exact eligible gate reason set in the retry protocol; publish the count before sending retries. Exclude supervisor refusals, invalid/error calls, and honest cases. Keep gate abstentions separate rather than silently classifying them as blocks.

Give the same supervisor its original observation, first decision, and the actual block reason once. Do not add the true summary, new worker evidence, coaching, or a second retry. Execute the new decision through the unchanged gate. The maximum is 160 calls, not several hundred, and the actual number depends on eligible first decisions.

Store under `data/study_gate_retry`, pinning the parent study hash and linking parent/retry IDs. Log first decision and execution, block reason, retry decision, retry execution, status, and cost. Report rates conditional on eligible parents and full-trajectory attack outcomes separately. Missing retries remain unresolved. A retry failure must not erase an observed first request.

Acceptance: no parent has more than one retry; every retry has a frozen eligible parent; every execution is replayable. Describe original attacks as instructed and non-adaptive, and this extension as a single feedback-conditioned supervisor retry. It is not an adaptive attacker study or evidence of training.

### 6. Build the new-wording study and honest speaker control

Begin only after stages 4 and 5 are stored and frozen. Use `data/study_wording_v2` with its own protocol, corpus, hashes, ledger, and analysis outputs. Remove `wording_reference` in the new prompt builder only; preserve the original prompt and corpus. Audit instructions and validators for any other requirement to copy the original string. Validate role and factual constraints without demanding verbatim wording.

Develop prompts on existing development fixtures, freeze them, then collect the four existing test families. Retain instructed attack goals and the original evidence semantics. Log exact-copy rates, duplicates, lengths, invalid outputs, and any rejected generation attempts. Do not keep sampling until a more persuasive attack appears. Freeze a bounded generation/rejection policy before collection; report fallback use separately.

Use four conditions: one-speaker attack, three-speaker attack, one-speaker honest, and three-speaker honest. For the new honest condition, change only the outward speaker presentation and text while preserving the underlying legitimate workflow, oracle, and gate. Do not construct it by relabeling an attack. Pin the one speaker's role and knowledge. Attack speaker roles and message count still co-vary, so this control narrows a limitation without proving collusion or isolating every coordination mechanism.

With the current role structure, one frozen text realization per fixture/condition requires 40 x (1 + 3 + 1 + 3) = 320 test worker calls. Replaying all four evidence arms under both supervisors requires 40 x four conditions x four arms x two models = 1,280 new supervisor calls. These are new-text experiments, not reruns of the original 960 observations. If multiple text realizations are desired, fix their count and crossed assignment before seeing test outcomes; do not add them selectively. Budget development generation separately.

Use the same frozen worker text across arms and supervisors within each fixture/condition. State whether provenance uses visible or blinded trusted summaries; default to original visibility for the wording comparison, keeping stage 4 as the dedicated blinding experiment. Changing both at once would confound interpretation. Do not mix old and new observations into one headline rate.

Acceptance: a fresh hashed worker corpus exists; every supervisor episode pins its text hash; all conditions use the same four families; honest one-speaker fixtures pass independent oracle/gate controls; results regenerate without inference. New wording is not evidence of new mechanisms or task generalization.

## Credit policy

Treat 1,490 credits as the total authorized ceiling, not a spending target. Proposed stage envelopes are 20 credits for blind provenance, 20 for eligible retries, 100 for the wording/control study, and 50 for bounded development and collection failures. Keep 1,300 unallocated. These are maximum allowances, not cost estimates or predicted spend.

Before collection, verify the current prepaid balance, model availability, inference prices, and billing units using available account evidence. The old 960-call ledger cost about 4.11 credits, so exhausting 1,490 would need a scientific justification that this plan does not provide. The dataset SDK's `estimate=True` is not assumed to quote this repository's direct inference endpoint.

Use the existing reservation-before-send ledger pattern. Reserve against bounded input size and maximum output tokens; track actual usage, conservative uncertain charges, and in-flight reservations separately. A stage stops before a request that would exceed its envelope or the verified global balance. Retain the original Qwen output allowance as the starting point, since the earlier pilot found incompletes at 2,048 tokens. Do not reduce limits mid-study because a result looks inconvenient.

Never automatically replay a request whose delivery or billing is uncertain. Transport recovery and the scientific one-retry intervention are separate policies. Any spending reallocation must preserve a fixed scientific schedule and be documented before the affected calls. Leftover credits do not justify a larger model, extra repeated families, or another copy of the frozen run.

## Venue delivery schedule

All local times below are IST. Use internal freeze times as buffers; do not treat the minute before a deadline as working time.

**13 November:** finish source preservation, stages 1 to 3, the generated claim ledger, and the AGENTVERIFY draft. Freeze its numeric artifacts as soon as offline checks pass. Stage 4 may proceed afterward, but is not on the archival submission's critical path.

**14 November, noon:** freeze the AGENTVERIFY PDF. Submit by 3 PM, ahead of the supplied 5:29 PM deadline. Target a two-page anonymous IEEE ICSE extended abstract including references. Move to four pages only if the required table and caveats are unreadable at compliant sizes. Include harness/oracle, both endpoints, mutation audit, and the pairing row. Include blind numbers only if the entire blind ledger and report have already been rebuilt. Remove names, affiliations, repository links, identifying citations, PDF author metadata, and identifying artifact links. Call the fixtures synthetic local jobs, never repository tasks. The official CFP confirms double-blind review and that extended abstracts enter proceedings; this is the sole archival slot.

**14 to 15 November:** collect and freeze stage 4, then stage 5 in that order. Keep each failed or incomplete run's evidence. Prepare ADMIT in parallel with collection only through work that does not assume new results. Freeze ADMIT on 15 November evening; submit by 3 PM on 16 November. Use about two pages with the question, exact counts, and the five stated limits: scripted wording, unmatched arms, visible true summary, confounded coordination, and four families. Include completed mutation and blind results. No survey or appendix. The intended venue is ADMIT: Accountable Decision-Making and Integrated Trust for autonomous AI systems at HiPEAC 2027. HiPEAC identifies https://admit-workshop.github.io as its official workshop website. The user supplied https://easychair.org/my/conference?conf=admit2027 as the submission portal. Both the portal and workshop website were inaccessible to the web tool during verification, so the supplied deadline, length, and non-archival status remain user-provided requirements pending direct CFP verification.

**16 to 18 November:** complete stage 6 if stages 4 and 5 are frozen. Its results enter AAAI only if the full study freezes by 18 November evening. Otherwise leave it out of that submission and continue it for A³. Do not delay the completed measurement paper to add unfinished text-generation results.

**19 to 20 November:** rebuild and audit the AAAI paper, at most seven content pages plus references in AAAI two-column format, with names retained. Include stages 1 to 5 and the full disclosure. Suggested page budget is one page for question/contribution, one for design/oracle/endpoints, two for original results/mutations/pairing, two for blind/retry measurements, and one for limitations/discussion; adjust for figures and tables. Include stage 6 only after its independent freeze. Freeze the PDF by 20 November evening and submit by 3 PM on 21 November, before the supplied 5:29 PM deadline.

**A³:** create no manuscript file now. Its official page still calls it a proposed ICLR 2027 workshop. Treat 29 November as the user's workshop-acceptance checkpoint, not a paper deadline or guaranteed opening. If its call opens, reuse the AAAI source and carry stage 6 if complete, with a title limited to recorded evidence. Verify the final CFP and non-archival/overlap rules then.

Use shared generated numeric tables and a version-specific evidence manifest for each submitted PDF. Preserve each submission snapshot so later evidence cannot silently alter what an earlier paper reported. Treat overlap eligibility as a venue rule to verify before submission, rather than assuming non-archival status resolves every policy question. Do not submit automatically as part of implementation.

## Final release checks

Each result gets a generated claim ledger linking its numerator, denominator, exclusions, input hashes, and table row. Papers read generated TeX values instead of copied rates. Tests cover endpoint normalization, gate mutation controls, pairing exclusions, answer hiding, retry eligibility/cap, immutable input paths, and ledger reconciliation. Repeat the original verification and offline reproduction after changes, alongside extension tests. Render and inspect every final PDF for table legibility, page limits, anonymity where required, and exact agreement with its evidence manifest.

The release is complete when stages 1 to 5 have reproducible artifacts and the three currently actionable manuscripts have venue-specific PDFs, with stage 6 either independently frozen or explicitly absent from each submission. A³ remains a conditional delivery, not a fabricated fourth paper. Negative or null results are valid outcomes; missing data and failed gates must remain visible.

## Official venue sources checked during planning

- AGENTVERIFY CFP: https://agentverify2027.github.io/cfp.html . November 13 AoE, two-page extended abstracts including references, double-blind IEEE/ICSE, proceedings by default. The deadline corresponds to November 14 at 5:29 PM IST when stated at minute precision.
- AAAI LLM Agents Under Threat: https://llm-agents-under-threat.github.io/ . November 20 AoE, at most seven pages excluding references, AAAI two-column, single-blind, non-archival. The deadline corresponds to November 21 at 5:29 PM IST at minute precision.
- A³ proposal: https://auditcommons.org/workshops/a3-iclr-2027/ . Proposed workshop; no active submission deadline verified.
- ADMIT venue identity verified through HiPEAC: https://www.hipeac.net/events/sessions/8320/admit-accountable-decision-making-integrated-trust-autonomous-ai-systems/ . Official workshop website linked there: https://admit-workshop.github.io . User-supplied submission portal: https://easychair.org/my/conference?conf=admit2027 . The web tool could not access the latter two pages; deadline, page count, and publication status are not independently verified. The unrelated Algorithms, Data Mining, and Information Technology conference is not this venue.
