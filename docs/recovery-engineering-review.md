# Recovery pilot engineering review

Reviewed 10 November 2026 by the integrating coding agent after the delegated OpenCode review failed with a free usage limit. This is a code review and offline test record, not independent scientific validation. The collecting pilot source was not changed during this review.

## Checks completed

`tests/test_recovery_adversarial.py` adds five offline checks. Sixteen concurrent reservation workers cannot reserve more than the 20-credit cap; pending reservations also consume the 360-request limit. A changed record body, a replacement public key, and a missing archive bucket each fail before a supervisor request can use the evidence. All five checks passed.

The runner writes a reservation before sending a request and records failures conservatively at the reserved amount. Resume refuses pending calls rather than silently repeating potentially billed inference. The request artifacts contain the model payload, response, token usage and outcome, while HTTP authorization stays in the client. A release artifact is written before the hidden oracle scores its outcome, so the oracle does not decide whether execution occurs. All policies use this same sandbox sink.

## Limits that affect interpretation

The ledger lock protects threads within one collector process. It does not provide a cross-process lock. Run only one collector against an output directory; simultaneous collector processes are outside the tested contract. An interrupted write can leave a pending request requiring explicit reconciliation. Do not remove it to force a retry.

The signature covers the record body. Case and record identities are attached through the separately pinned source catalog and signature map, rather than signed inside an identity-and-sequence envelope. The source hashes and archive-map hash are checked at collection startup. This is an in-process catalog authentication check, not a separately enforced production logging boundary or a general replay-resistant archive protocol.

Archive metadata includes the body digest and byte count for every record, even on claims-only calls. The observation also names the evidence policy. These are part of the pilot information boundary and can provide shortcuts. A later evaluation should audit metadata-only performance, blind policy names if unnecessary, and distinguish record identity from outcome-correlated metadata. The pilot is not an isolated causal estimate of reading an additional body.

The executable oracle only loads allowlisted catalog source strings. That restriction is essential: it is not a sandbox for arbitrary live model-generated Python. A fresh worker code study needs an independently constrained execution boundary, resource limits, and tests outside the worker-visible context.

No API retry is automatic. The 60-second timeout can classify a slow request as failed even if the provider bills it; that request is charged at its conservative reservation. Charged ledger credits and usage-derived estimates should be reported separately. The collection cap is not a matched oversight-policy budget.

## Required attack coverage for the sequential study

- Omit recorded bodies while preserving authenticated missing-record metadata, and compare random with policy-aware omission on the same original trajectory.
- Remove a body from the archive entirely and distinguish failure to record from archive unavailability; neither is successful recovery.
- Mutate bodies, identities, kinds and sequence positions, including replay of an authentic body in the wrong workflow. Verify an identity-bound envelope rather than the body signature alone.
- Allow a compromised signer to authenticate false content and show that provenance alone cannot establish semantic correctness.
- Attack the full queue and shared review/retrieval allowance with permitted work and strategically timed violations. Count legitimate work held after exhaustion as lost completion.
- Preserve every unknown, failed and invalid outcome in all-status denominators. Evaluate the complete stopping policy rather than treating individual prefix decisions as independent calibrated trials.

These regimes are requirements for the next study, not attacks executed by the current fixed-policy pilot.
