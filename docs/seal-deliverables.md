# SEAL deliverables and rebuild commands

This is the deliverable registry, not a claim that an active collection has
finished. `build/seal/studies_complete.json` records completed extension
freezes; `build/seal/all_reproduction.json` records the complete offline rebuild.

## Manuscripts

| Version | Source | PDF | Format |
|---|---|---|---|
| Main preprint | `paper/main.tex` | `paper/main.pdf` | User-selected Overleaf arXiv style |
| AGENTVERIFY | `paper/venues/agentverify/agentverify.tex` | `paper/venues/agentverify/agentverify.pdf` | Anonymous IEEE/ICSE, two pages including references |
| ADMIT | `paper/venues/admit/admit.tex` | `paper/venues/admit/admit.pdf` | Named one-page presentation proposal, no required template |
| AAAI Agents Under Threat | `paper/venues/aaai/aaai.tex` | `paper/venues/aaai/aaai.pdf` | Named AAAI two-column, at most seven content pages excluding references |

A³ has no manuscript file. Its call is not open; reuse the AAAI source after
the call opens and its final requirements are verified. This is intentional.

ADMIT's official one-page requirement supersedes the approximate two-page
assumption in the initial plan. Template provenance is in
`paper/templates/SOURCES.md`. The main manuscript's `arxiv.sty` matches the
upstream style linked by the requested Overleaf template byte for byte.

The original fixed-study report template is preserved as `paper/legacy_main.tex`
when the main preprint is advanced to the extensions. Its original generator
uses that template explicitly, so the old report and the extended manuscript
do not silently substitute for each other.

## Evidence

- `data/study`: immutable original 960-call record.
- `data/study_blind_provenance`: the separate 240-call summary-hidden study.
- `data/study_gate_retry`: one retry per eligible blocked parent, never more than 160.
- `data/study_wording_v2`: retained diagnostic corpus with extensive HTTP 429 failures
  and eight interrupted supervisor deliveries, conservatively settled without replay.
- `data/study_wording_v3`: one paced primary replication on the same four families,
  selected for operational failure before observing its safety outcomes.
- `data/wording_cohort_selection.json`: primary/diagnostic assignment, diagnostic
  checksum, driver hash, and the shared 100-credit wording ceiling.
- `data/account_evidence`: model and official price evidence; the available balance
  is explicitly user-reported, not API verified.

Each completed extension has its own protocol, source/prompt hashes, saved calls,
terminal ledger, and checksum manifest. Collection source snapshots are retained.
Missing and invalid decisions remain visible. No original call is rerun to fill
an old missing cell. All numerical tables are generated from code.

The wording protocol uses one attempt per message, with no regeneration or
fallback. Exact copies are counted without rejection. Accepted text is frozen
before any supervisor arm sees it. A missing condition remains in the planned
supervisor denominator. One-speaker honest controls preserve the legitimate
workflow and oracle while changing the outward speaker presentation.
Both wording cohorts count toward the same budget, and neither is pooled with
the historical corpus. Schema, role, and supplied-total token checks determine
acceptance; these are not independent semantic annotation. Separate amendments
record the string-total validator repair, missing blind response bodies, and
interrupted delivery. The original pending bytes are preserved in a checkpoint.
A separate Windows storage amendment records one reservation that failed before
call-file creation and HTTP dispatch. Its original ledger and traceback are
preserved. That proven-unsent reservation was released; only local file writes
receive bounded retries, and durable pending API calls remain ineligible for replay.

## Rebuild without an API key

Verify the original input and source contract:

```text
python scripts/reproduce.py --verify-only
```

Rebuild the original analysis, two endpoints, mutation sweep, and pairing coverage:

```text
python scripts/reproduce.py --analysis-only
```

After all extension studies are frozen, rebuild their analyses and all current
manuscripts without making model calls:

```text
python scripts/reproduce_all.py
python scripts/audit_seal_release.py
```

The full rebuild refuses unfrozen extensions. Individual short-paper generators
remain usable without optional blind numbers; missing data is absent, never zero.
The AAAI and main manuscript generator requires frozen blind and retry studies;
it includes the wording study only when independently frozen.

Run verification tests:

```text
python -m pytest -q
```

The final archive builder refuses an extension directory that exists but is not
frozen. It includes the original record, independent extensions, source snapshots,
configuration, generated tables, and venue artifacts. It performs no upload or
external submission.

## Scope of claims

Claims, receipts, and the fixed snapshot still publish completed attacks in this
catalog. Provenance execution zeros come from the code gate, including its
`trusted_summary != summary` check and earlier binding/authorization vetoes;
correcting the summary does not make an illegitimate fixture legitimate.
The original provenance observations showed the true summary when available.
All 280 accepted original worker calls copied `wording_reference` from the prompt.
The original paired counts remain Gemma 2/4 in both attack strata, Qwen 0/4 single
and 1/4 coordinated, with `incident-timeline` unpaired in every attack stratum.
Attacks are instructed, the sink is fake and local, `side_effects` is false, and
signing keys are public demo keys. No result establishes learned refusal,
general receipt weakness, spontaneous collusion, or broad task generalization.

No paper has been externally submitted by these scripts. AGENTVERIFY is the
single intended archival submission; the other venue versions are non-archival.
