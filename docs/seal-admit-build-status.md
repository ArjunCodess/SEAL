# SEAL ADMIT build status

## Venue

- Intended venue: ADMIT: Accountable Decision-Making and Integrated Trust for Autonomous AI Systems at HiPEAC 2027.
- Official workshop website: https://admit-workshop.github.io
- HiPEAC listing: https://www.hipeac.net/events/sessions/8320/admit-accountable-decision-making-integrated-trust-autonomous-ai-systems/
- User-supplied submission portal: https://easychair.org/conferences/?conf=admit2027
- Official deadline: 15 November 2026, 23:59 AoE (16 November 2026, 17:29 IST).

## Official format verified

The official CFP was accessible during this build. It requests a **one-page PDF presentation proposal**
containing the presentation title, author name(s) and affiliation(s), and a short description of the
challenge, insight, or lesson. No template is required, there are no formal proceedings, and previously
published or presented material is welcome.

This differs from the earlier planning assumption of about two pages. The generated PDF is therefore
kept to one page. The official CFP supersedes the earlier two-page assumption.

## Build outputs

- Paper source: `paper/venues/admit/admit.tex`
- Generated macros and evidence: `build/venues/admit/generated.tex`, `build/venues/admit/evidence_manifest.json`, `build/venues/admit/claim_to_evidence.json`
- PDF: `build/venues/admit/admit.pdf`
- Build status: `build/venues/admit/build_status.json`
- Author block: `build/venues/admit/authors.tex`

## Page count and format confidence

- Compiled PDF page count: 1.
- Format confidence: high for the one-page proposal requirement; the PDF uses a plain legible academic
  layout because the CFP states that no template is required.
- Warnings: the official CFP could not be independently rechecked after this build; if the CFP changes,
  regenerate with `scripts/prepare_seal_admit.py`.

## Numeric provenance

All counts in the paper are generated from:

- `build/seal/analysis/two_endpoints.csv`
- `build/seal/analysis/pairing_coverage.csv`
- `build/seal/analysis/pairing_audit.csv`
- `build/gate_mutations.csv`
- `build/gate_mutations_manifest.json`

No rates were hand-edited. The evidence manifest records SHA-256 hashes for these inputs.

## Blind provenance status

Blind provenance results are excluded from this draft. The blind study is not frozen or checksum-verified,
so `--include-blind` is refused unless a complete verified study exists. Missing data is treated as absent,
not zero.

## QA evidence commands

```powershell
python scripts/prepare_seal_admit.py --compile
python -m pytest tests/test_seal_admit.py -q
python -c "from pypdf import PdfReader; print(len(PdfReader('build/venues/admit/admit.pdf').pages))"
```

## Submission status

Not externally submitted. No commit, push, or external message was performed. The user may submit the
generated one-page PDF through the EasyChair portal.