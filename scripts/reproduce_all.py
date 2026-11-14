"""Rebuild every completed SEAL study and manuscript without inference access."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import seal_collection as sc, seal_followups as follow
from scripts import reproduce, analyze_seal_followups, prepare_seal_paper, prepare_seal_admit
from scripts.prepare_seal_venues import prepare_venue
from scripts import audit_seal_release


def run():
    # Require every intended extension before changing final deliverables.
    parents = {name: follow.require_frozen(ROOT / "data" / name) for name in
               ("study_blind_provenance", "study_gate_retry", "study_wording_v2", "study_wording_v3")}
    from scripts.seal_wording_cohorts import frozen_cohorts
    if 'primary' not in frozen_cohorts():
        raise ValueError('The preselected primary wording cohort is not frozen')
    result = reproduce.reproduce(ROOT / "build", analysis_only=True)
    result['release_audit'] = audit_seal_release.run()
    sc.build_comparison(study_dir=ROOT / "data/study_blind_provenance")
    analyze_seal_followups.run(ROOT / "build/seal/analysis")
    result["agentverify"] = prepare_venue(venue="agentverify", build_dir=ROOT / "build/venues/agentverify",
        paper_dir=ROOT / "paper/venues/agentverify", compile_pdf_flag=True)
    result["admit"] = prepare_seal_admit.run(
        analysis_dir=ROOT / "build/seal/analysis", gate_csv=ROOT / "build/gate_mutations.csv",
        gate_manifest=ROOT / "build/gate_mutations_manifest.json", authors_path=ROOT / "paper/authors.json",
        output_dir=ROOT / "build/venues/admit", paper_dir=ROOT / "paper/venues/admit",
        include_blind=True, blind_dir=ROOT / "data/study_blind_provenance", compile_pdf_flag=True)
    result["main_and_aaai"] = prepare_seal_paper.run()
    result["parents"] = parents
    result["inference_calls"] = 0
    import pymupdf
    result['pdf_quality'] = {}
    for venue, limit in (("main", None), ("aaai", None), ("agentverify", 2), ("admit", 1)):
        folder = ROOT / 'paper' if venue == 'main' else ROOT / 'paper/venues' / venue
        pdf = folder / f'{venue}.pdf'
        document = pymupdf.open(pdf)
        if limit is not None and len(document) > limit:
            raise ValueError(f"{venue} exceeds its {limit}-page limit")
        text = ' '.join(page.get_text() for page in document)
        normalized = ' '.join(text.casefold().split())
        if '??' in text:
            raise ValueError(f'{venue} has an unresolved reference')
        if venue == 'agentverify':
            author = sc.read_json(ROOT / 'paper/authors.json')['authors'][0]
            if any(term.casefold() in normalized for term in
                   (author['name'], author['affiliation'], 'github.com')):
                raise ValueError('AGENTVERIFY anonymity check failed')
        build = ROOT / 'build/venues' / venue
        for previous in build.glob('*.png'):
            match = re.fullmatch(r'(?:page_|final-|layout-|review_)(\d+)\.png', previous.name)
            if match and int(match[1]) > len(document):
                previous.unlink()
        for index, page in enumerate(document):
            if any(not page.rect.contains(pymupdf.Rect(*word[:4])) for word in page.get_text('words')):
                raise ValueError(f'{venue} has text outside a page boundary')
            page.get_pixmap(matrix=pymupdf.Matrix(1.3, 1.3)).save(build / f'final-{index+1}.png')
        result['pdf_quality'][venue] = {'pages': len(document), 'sha256': sc.sha256_file(pdf),
                                      'unresolved_references': False, 'text_within_page': True,
                                      'anonymity_checked': venue == 'agentverify'}
    follow.write_json(ROOT / "build/seal/all_reproduction.json", result)
    audit = result['release_audit']
    lines = ['# SEAL completion status', '',
        'Generated from frozen ledgers and the successful offline rebuild. A frozen study retains invalid and missing outcomes; it does not imply that every call succeeded.', '',
        'The original 960 calls and five pinned sources verify unchanged. The blind arm, one gate retry, and both separately identified wording cohorts are frozen. No external submission was performed.', '',
        '| Study | Attempted calls | Parsed result statuses | Conservative credits |',
        '|---|---:|---|---:|']
    blind = audit['studies']['blind']
    lines.append(f"| Blind provenance | 240 | {blind['freeze_manifest']['status_counts']} | {blind['charged_credits']:.8f} |")
    retry = audit['studies']['retry']
    lines.append(f"| One gate retry | {retry['calls']} | {retry['result_statuses']} | {retry['charged_credits']:.8f} |")
    for cohort in ('diagnostic', 'primary'):
        for phase, row in audit['studies'][f'wording_{cohort}']['parts'].items():
            lines.append(f"| Wording {cohort}, {phase} | {row['calls']} | {row['result_statuses']} | {row['charged_credits']:.8f} |")
    lines += ['', f"Extension charges total **{audit['extensions_charged_credits']:.8f} credits** against the user-authorized 1,490-credit remaining ceiling. Charges use recorded usage when available and full reservations for errors or uncertain delivery. This is not an API-verified account balance, and the historical original spend was not subtracted again.", '',
        'The diagnostic wording cohort retains 232 worker HTTP 429 failures and eight uncertain supervisor deliveries. One paced primary replication was selected for this operational failure before observing its safety outcomes. Neither cohort is pooled with the original record. One generation attempt is allowed per message; unavailable groups stay in the planned 1,280-cell supervisor grid.', '',
        '| Manuscript | PDF | Pages |', '|---|---|---:|']
    for venue, folder in (('main', 'paper'), ('agentverify', 'paper/venues/agentverify'),
                          ('admit', 'paper/venues/admit'), ('aaai', 'paper/venues/aaai')):
        path = ROOT / folder / f'{venue}.pdf'
        lines.append(f"| {venue} | [{venue}.pdf](../{folder}/{venue}.pdf) | {len(pymupdf.open(path))} |")
    lines += ['', 'AGENTVERIFY is anonymous and is the sole archival venue. ADMIT follows the verified one-page proposal format. AAAI uses its official named two-column style. The main preprint uses the requested arXiv template. A³ has no manuscript because its call is not open.', '',
        'Every reported rate is rebuilt from code. Models and speaker conditions remain separate. Provenance execution zeros come from the unchanged code gate, not learned refusal. The original wording and pairing limits remain in every manuscript.', '',
        'Rebuild with `python scripts/reproduce_all.py`; inspect `build/seal/release_audit.json` and `build/seal/all_reproduction.json`. Packaging is separate: `python scripts/package_release.py`.', '']
    (ROOT / 'docs/seal-completion-status.md').write_text('\n'.join(lines), encoding='utf8')
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
