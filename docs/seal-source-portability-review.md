# SEAL source-portability review (venue artifacts)

Date: 2026-11-14. Scope: read-only portability and format review of the four
paper deliverables plus template provenance. No manuscript/generator/data
edits, no final PDF writes, no commits, no model/API calls. Disposable compile
copies were made under the system temp directory only. Sole permitted repo
edits made: this file and `tests/test_seal_source_portability.py`.

## Verdict

All four deliverables compile standalone from their own source folders,
independently of `build/`, and meet their page caps and anonymity rules.
No extra template is needed from the user. No blocking defects found; minor
cosmetic/provenance notes below.

## Standalone compile results

Each deliverable was copied into a disposable temp folder with **only** its
own source set and compiled there with the venue engine.

| Deliverable | Engine | Exact command (run in the copied folder) | Result |
|---|---|---|---|
| `paper/main.tex` | MiKTeX pdflatex | `pdflatex -interaction=nonstopmode -halt-on-error -output-directory=out main.tex` (run twice) | OK, 6 pages, clean log |
| `paper/venues/aaai/aaai.tex` | MiKTeX pdflatex | same command with `aaai.tex` | OK, 6 pages total |
| `paper/venues/agentverify/agentverify.tex` | `.tools\tectonic.exe` | `tectonic agentverify.tex --outdir out` | OK, 2 pages |
| `paper/venues/admit/admit.tex` | `.tools\tectonic.exe` | `tectonic admit.tex --outdir out` | OK, 1 page |

Source sets used per deliverable (nothing else was present in the copy):

- main: `main.tex`, `arxiv.sty`, `seal_measurement.pdf` — no `\input`/`build/`
  references in `main.tex` (body is inline).
- AGENTVERIFY: `agentverify.tex`, `IEEEtran.cls`, `generated/{macros,abstract,
  table_endpoints,table_mutations,table_pairing,table_blind}.tex`.
- ADMIT: `admit.tex`, `live_generated/{generated,authors,abstract,
  table_endpoints,table_blind}.tex` — confirms the repaired state compiles
  using only `paper_dir` contents.
- AAAI: `aaai.tex`, `aaai2027.sty`, `aaai2027.bst` (bst shipped per CFP; the
  manuscript uses an inline `thebibliography`, so bibtex is not required).

Cross-check: PDFs recompiled from the copies have extracted text identical to
the delivered `paper/main.pdf`, `venues/aaai/aaai.pdf`,
`venues/agentverify/agentverify.pdf`, `venues/admit/admit.pdf` (SHA-256
differences are embedded creation timestamps only), so the delivered PDFs
match the current sources.

## Caps, references, anonymity

- AGENTVERIFY: 2 pages (= CFP cap, references included). Extracted text
  contains no "Arjun"/"Prakash"/"Independent Researcher"/"github.com"; author
  block is `Anonymous Submission`; PDF metadata author is `Anonymous`;
  bibliography has no identifying self-citation (AgentDojo, Instruction
  Hierarchy, AI Control, CaMeL). Unresolved-reference markers (`??`): none.
- ADMIT: exactly 1 page (CFP: one-page presentation proposal, no template
  required). Author name/affiliation retained as intended.
- AAAI: 6 pages total; `References` heading begins on page 6, so content is
  5 full pages plus part of page 6 — under the 7-content-page cap (same
  counting rule as `prepare_seal_paper.py`). Author retained (single-blind).
  9 underfull hboxes, including one badness-10000 in the wording-table
  region (lines ~186–244): cosmetic only; zero overfull hboxes.
- main (arXiv preprint): 6 pages, no warnings, no overfull boxes, author
  retained.

## Template provenance under `paper/templates` (hash-verified)

- `arxiv.sty`: SHA-256 `5ca551db…1dc46` — matches the byte-for-byte upstream
  comparison and hash recorded in `templates/SOURCES.md` (Kour arXiv style).
- `ieee/IEEEtran.cls`: SHA-256 matches `ieee/source.json`
  (`da751920…a3bd7`); venue copy is identical. Class used unmodified with
  explicit `10pt,conference` options as the CFP requires.
- `aaai2027/aaai2027.sty` + `.bst`: template and venue copies are identical
  (sty `01990f8d…`, bst `5db7765b…`); extracted from the official author kit
  (`https://aaai.org/authorkit27/`, archive hash `9406a545…` recorded in
  `aaai2027/source.json`). Minor gap: per-file hashes are not recorded in
  `source.json` (only the archive hash), so sty/bst identity currently relies
  on the template/venue copy being identical, which it is.
- ADMIT: no template needed by its CFP (plain `article` + `geometry`), so no
  provenance artifact applies.
- AAAI style option: `aaai.tex` uses `\usepackage[preprint]{aaai2027}`
  (authors shown, no anonymous-submission footer) — consistent with the
  workshop's single-blind review per `SOURCES.md`. If AAAI's submission
  system later asks for `[submission]`, that is a one-word change.

## A³ status

No A³ manuscript or template artifact exists in `paper/` (searched; only
hash-named frozen episode files in `build/` matched textually, which are not
manuscripts), and the generators actively delete stale `A3.tex` files. This
satisfies "no A³ manuscript while the CFP is not open". No template should be
added for A³ until its call opens.

## Staleness / parent-rebuild note

`venues/agentverify` and `venues/admit` artifacts date from 00:48, before the
parent's 05:59 `prepare_seal_paper.py` wording-table change (per-model
one-column tables). Both venues are unaffected by that change: AGENTVERIFY
explicitly excludes new-wording results and ADMIT has no wording section, and
their sources are unchanged since 00:48, so the delivered PDFs still match
their sources (verified by recompile text-equality above). `main.tex`/`aaai.tex`
were rebuilt at 05:59 with the new wording tables
(`build/seal/paper_layout.stdout.log`: 6 pages each). Final visual QA and
rebuild remain with the parent; current PDFs are layout candidates.

## Defects / notes

1. Cosmetic: AAAI underfull hboxes (see above) — parent visual QA may or may
   not act; no overflow risk detected (no overfull boxes anywhere).
2. Cosmetic: AGENTVERIFY under Tectonic/XeTeX logs `TU/ptm` font-shape
   substitutions ("defaults substituted") and "inputenc ignored with utf8
   based engines" — benign; output is 2 clean pages. Delivered artifact was
   produced the same way.
3. Provenance (minor): `aaai2027/source.json` records only the author-kit
   archive hash; consider adding per-file sty/bst hashes.
4. No unresolved references, missing files, or `build/` path dependencies in
   any of the four sources.
5. Extra template needed from user: **No.**

## Regression test added

`tests/test_seal_source_portability.py` (offline, pytest): copies each
deliverable's exact source set to a temp dir, compiles with the venue engine
(skips if pdflatex/tectonic unavailable), and asserts page caps
(AGENTVERIFY ≤ 2, ADMIT = 1, AAAI content ≤ 7 excluding references), no `??`
unresolved references, author retained on main/aaai/admit, AGENTVERIFY
anonymity (text + PDF metadata), and no `build/` inputs in sources. Runs
read-only; no writes to `paper/` or `build/`. Result: **4 passed**
(`python -m pytest tests/test_seal_source_portability.py`).
