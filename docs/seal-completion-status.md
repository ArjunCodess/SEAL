# SEAL completion status

Generated from frozen ledgers and the successful offline rebuild. A frozen study retains invalid and missing outcomes; it does not imply that every call succeeded.

The original 960 calls and five pinned sources verify unchanged. The blind arm, one gate retry, and both separately identified wording cohorts are frozen. No external submission was performed.

| Study | Attempted calls | Parsed result statuses | Conservative credits |
|---|---:|---|---:|
| Blind provenance | 240 | {'completed': 228, 'failed': 6, 'invalid': 6} | 1.29058114 |
| One gate retry | 114 | {'completed': 113, 'error': 1} | 0.54697127 |
| Wording diagnostic, workers | 320 | {'completed': 55, 'invalid': 33, 'error': 232} | 0.19557549 |
| Wording diagnostic, supervisors | 184 | {'error': 42, 'completed': 139, 'invalid': 3} | 1.23816602 |
| Wording primary, workers | 320 | {'invalid': 106, 'completed': 201, 'error': 13} | 0.02566512 |
| Wording primary, supervisors | 632 | {'completed': 565, 'error': 44, 'invalid': 23} | 3.09576018 |

Extension charges total **6.39271922 credits** against the user-authorized 1,490-credit remaining ceiling. Charges use recorded usage when available and full reservations for errors or uncertain delivery. This is not an API-verified account balance, and the historical original spend was not subtracted again.

The diagnostic wording cohort retains 232 worker HTTP 429 failures and eight uncertain supervisor deliveries. One paced primary replication was selected for this operational failure before observing its safety outcomes. Neither cohort is pooled with the original record. One generation attempt is allowed per message; unavailable groups stay in the planned 1,280-cell supervisor grid.

| Manuscript | PDF | Pages |
|---|---|---:|
| main | [main.pdf](../paper/main.pdf) | 6 |
| agentverify | [agentverify.pdf](../paper/venues/agentverify/agentverify.pdf) | 2 |
| admit | [admit.pdf](../paper/venues/admit/admit.pdf) | 1 |
| aaai | [aaai.pdf](../paper/venues/aaai/aaai.pdf) | 4 |

AGENTVERIFY is anonymous and is the sole archival venue. ADMIT follows the verified one-page proposal format. AAAI uses its official named two-column style. The main preprint uses the requested arXiv template. A³ has no manuscript because its call is not open.

Every reported rate is rebuilt from code. Models and speaker conditions remain separate. Provenance execution zeros come from the unchanged code gate, not learned refusal. The original wording and pairing limits remain in every manuscript.

Rebuild with `python scripts/reproduce_all.py`; inspect `build/seal/release_audit.json` and `build/seal/all_reproduction.json`. Packaging is separate: `python scripts/package_release.py`.
