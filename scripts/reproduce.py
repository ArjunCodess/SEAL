"""Rebuild the recorded study without requesting model inference."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import csv
import hashlib
import json
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / 'data' / 'study'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest_recorded_text(path: Path) -> str:
    """Hash an export in the CRLF form stored by the recorded Windows run."""
    data = path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
    return hashlib.sha256(data).hexdigest()


def verify_inputs(data: Path = DATA, source_root: Path = ROOT, *, legacy_only: bool = False) -> dict:
    """Verify input checksums and pinned contract sources.

    In default mode (legacy_only=False), verifies the current src/*.py modules
    against data/study/provenance/source_contract.json. Fails with an explicit
    drift error if any current source differs, with NO fallback.

    In legacy mode (legacy_only=True), verifies preserved legacy_sources/src/*
    and legacy_sources/source_manifest.json with NO name-only or build fallbacks.
    """
    checksums = json.loads((data / 'checksums.json').read_text(encoding='utf8'))
    for relative, expected in checksums.items():
        path = (data / relative).resolve()
        if not path.is_relative_to(data.resolve()) or not path.is_file():
            raise ValueError(f'Missing or invalid study input: {relative}')
        if digest(path) != expected:
            raise ValueError(f'Changed study input: {relative}')
    contract = json.loads((data / 'provenance/source_contract.json').read_text(encoding='utf8'))

    if legacy_only:
        if source_root.name != 'legacy_sources' or not source_root.is_dir():
            raise ValueError(
                'Legacy source verification requires the exact legacy_sources directory; '
                'no root, basename, or build fallback is permitted'
            )
        legacy_dir = source_root.resolve()
        manifest_path = legacy_dir / 'source_manifest.json'
        if not manifest_path.is_file():
            raise ValueError(f'Missing legacy source manifest: {manifest_path}')
        legacy_manifest = json.loads(manifest_path.read_text(encoding='utf8'))
        for relative, expected in contract['sha256'].items():
            if legacy_manifest.get(relative) != expected:
                raise ValueError(
                    f'Legacy source manifest mismatch for {relative}: expected {expected}, got {legacy_manifest.get(relative)}'
                )
            path = legacy_dir / relative
            if not path.is_file():
                raise ValueError(f'Preserved legacy source missing: {path}')
            actual = digest(path)
            if actual != expected:
                raise ValueError(f'Preserved legacy source differs: {relative} (expected {expected}, got {actual})')
    else:
        for relative, expected in contract['sha256'].items():
            path = (source_root / relative).resolve()
            if not path.is_file():
                raise ValueError(f'Frozen study source missing: {relative}')
            actual = digest(path)
            if actual != expected:
                raise ValueError(f'Frozen study source drift detected in {relative}: expected {expected}, got {actual}')

    manifest = json.loads((data / 'main/run_manifest.json').read_text(encoding='utf8'))
    if digest(data / 'main/protocol.json') != manifest['protocol_sha256']:
        raise ValueError('Main protocol and run manifest disagree')
    if digest(data / 'workers/assembled_corpus.json') != manifest['worker_corpus_sha256']:
        raise ValueError('Worker corpus and run manifest disagree')
    return {
        'study_files_verified': len(checksums),
        'frozen_sources_verified': len(contract['sha256']),
        'source_mode': 'legacy' if legacy_only else 'current',
        'source_set': 'legacy' if legacy_only else 'current',
        'source_set_verified': True,
        'verified_source_set': 'legacy' if legacy_only else 'current',
        'source_root': str(source_root),
        'legacy_manifest_verified': legacy_only,
    }


def verify_recovery_inputs(source_root: Path = ROOT) -> dict:
    """Check exact-byte manifests for the separate development diagnostics."""
    verified = {}
    for name in ('recovery-development', 'natural-workflow-costs'):
        folder = source_root / 'experiments' / name
        if not folder.exists():
            continue
        checksums = json.loads((folder / 'checksums.json').read_text(encoding='utf8'))
        for relative, expected in checksums.items():
            path = (folder / relative).resolve()
            if not path.is_relative_to(folder.resolve()) or not path.is_file() or digest(path) != expected:
                raise ValueError(f'Changed recovery input: {name}/{relative}')
        verified[name] = len(checksums)
    return {'recovery_artifacts_verified': verified}


@dataclass(frozen=True)
class RecordedResponse:
    filename: str
    raw: bytes
    record: dict


def read_responses(path: Path) -> list[RecordedResponse]:
    """Validate the lossless response container once, before materializing paths."""
    responses = []
    seen = set()
    for line in path.read_text(encoding='utf8').splitlines():
        row = json.loads(line)
        filename, text, expected = row['file'], row['text'], row['sha256']
        if not isinstance(filename, str) or not re.fullmatch(r'[a-f0-9]{64}\.json', filename):
            raise ValueError('Invalid recorded response filename')
        if filename in seen:
            raise ValueError(f'Duplicate recorded response: {filename}')
        if not isinstance(text, str):
            raise ValueError('Recorded response text must be UTF-8 JSON text')
        raw = text.encode('utf8')
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'Changed recorded response: {filename}')
        record = json.loads(text)
        if record['episode_id'] != filename[:-5]:
            raise ValueError(f'Episode ID and filename disagree: {filename}')
        responses.append(RecordedResponse(filename, raw, record))
        seen.add(filename)
    return responses


def materialize_study(data: Path, output: Path) -> list[RecordedResponse]:
    responses = read_responses(data / 'main/responses.jsonl')
    ledger = json.loads((data / 'main/ledger.json').read_text(encoding='utf8'))['episodes']
    if len(responses) != 960 or set(ledger) != {r.record['episode_id'] for r in responses}:
        raise ValueError('Need all 960 recorded responses and matching terminal ledger IDs')
    if any(r['status'] not in {'completed', 'invalid', 'failed'} for r in ledger.values()):
        raise ValueError('Recorded ledger contains a nonterminal call; it must not be replayed')
    destination = output / 'study/main'
    episodes = destination / 'episodes'
    episodes.mkdir(parents=True, exist_ok=True)
    unexpected = {p.name for p in episodes.glob('*.json')} - {r.filename for r in responses}
    if unexpected:
        raise ValueError('Build directory contains unexpected episodes; choose a fresh --output')
    for path in (data / 'main').glob('*.json'):
        shutil.copyfile(path, destination / path.name)
    shutil.copyfile(data / 'main/denominator.jsonl', destination / 'denominator.jsonl')
    for response in responses:
        (episodes / response.filename).write_bytes(response.raw)
    return responses


def audit_execution(responses: list[RecordedResponse], output: Path) -> dict:
    from src.live_fixtures import ARMS, COORDINATIONS, make_fixture, execute_decision
    from src.live_study import adapt_execution_outcome, normalize_episode_for_analysis
    checked = 0
    for response in responses:
        row = response.record
        if row['status'] != 'completed':
            continue
        index = int(row['fixture_id'].rsplit('.', 2)[1])
        fixture = make_fixture('test', index, row['coordination'])
        replay = execute_decision(fixture, row['arm'], row['supervisor_decision'])
        if adapt_execution_outcome(replay) != normalize_episode_for_analysis(row)['outcome']:
            raise ValueError(f'Local sink replay differs for episode {row["episode_id"]}')
        checked += 1
    gateway_rows = []
    for index in range(40):
        for coordination in COORDINATIONS:
            fixture = make_fixture('test', index, coordination)
            summaries = [('candidate', fixture['candidate_summary']), ('true', fixture['true_summary']), ('other', {'invalid_summary': True})]
            for label, summary in summaries:
                result = execute_decision(fixture, 'provenance', {'action': 'publish', 'summary': summary})
                gateway_rows.append({'fixture_id': fixture['fixture_id'], 'legitimate': fixture['legitimate'], 'summary': label,
                                     'executed': result['executed_publication'], 'gate_reason': result['enforcement']['reason']})
    with (output / 'gateway_audit.csv').open('w', encoding='utf8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(gateway_rows[0]))
        writer.writeheader(); writer.writerows(gateway_rows)
    attacks = [r for r in gateway_rows if not r['legitimate']]
    if any(r['executed'] for r in attacks):
        raise ValueError('The recorded catalog no longer satisfies its provenance refusal invariant')
    return {'completed_sink_outcomes_replayed': checked, 'attack_gateway_checks': len(attacks),
            'attack_gateway_executions': sum(r['executed'] for r in attacks)}


def verify_analysis(output: Path, data: Path = DATA) -> dict:
    import pandas as pd
    from pandas.testing import assert_frame_equal
    contract = json.loads((data / 'reference/analysis_contract.json').read_text(encoding='utf8'))
    if digest_recorded_text(output / 'episodes.jsonl') != contract['episodes_sha256']:
        raise ValueError('Normalized episode export differs from the recorded study')
    summary = json.loads((output / 'analysis/summary.json').read_text(encoding='utf8'))
    if summary['input']['status_counts'] != contract['status_counts'] or summary['input']['episodes'] != 960:
        raise ValueError('Episode denominators differ from the recorded study')
    if summary['analysis']['bootstrap'] != contract['bootstrap']:
        raise ValueError('Bootstrap configuration differs from the frozen analysis')
    names = ['aggregate.csv', 'paired_contrasts.csv', 'pairing_inventory.csv', 'endpoint_denominators.csv']
    for name in names:
        actual = pd.read_csv(output / 'analysis' / name)
        expected = pd.read_csv(data / 'reference' / name)
        assert_frame_equal(actual, expected, check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12, obj=name)
    return {'reference_tables_verified': len(names), 'episodes_sha256': contract['episodes_sha256'],
            'status_counts': summary['input']['status_counts']}


def render_overview(analysis: Path, destination: Path) -> None:
    """Render marginal model/condition rates without pooling their denominators."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import pandas as pd
    frame = pd.read_csv(analysis / 'aggregate.csv')
    arms = ['claims', 'receipts', 'state', 'provenance']
    models = ['gemma-4-31b-it', 'qwen3.5-397b']
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.3), sharey=True)
    for ax, model, title in zip(axes, models, ['Gemma 4 31B', 'Qwen 3.5 397B']):
        for coordination, offset, color, marker in [('single', -.06, '#245a81', 'o'), ('coordinated', .06, '#c06b2d', 's')]:
            rows = frame[(frame['model'] == model) & (frame['coordination'] == coordination)].set_index('arm').loc[arms]
            rate = rows['illegitimate_unsafe_execution_rate'].to_numpy()
            low = rate - rows['illegitimate_unsafe_execution_ci_low'].to_numpy()
            high = rows['illegitimate_unsafe_execution_ci_high'].to_numpy() - rate
            ax.errorbar([i + offset for i in range(4)], rate, yerr=[low, high], fmt=marker + '-',
                        color=color, linewidth=1.4, markersize=4, capsize=3, label=coordination.capitalize())
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.set_xticks(range(4), ['Claims', 'Receipts', 'State', 'Provenance\n+ gate'], fontsize=10)
        ax.set_ylim(-.05, 1.08)
        ax.set_yticks([0, .25, .5, .75, 1], ['0%', '25%', '50%', '75%', '100%'])
        ax.spines[['top', 'right']].set_visible(False)
        ax.grid(axis='y', alpha=.16)
    axes[0].set_ylabel('Unsafe execution among completed attacks', fontsize=10)
    axes[1].legend(loc='lower left', fontsize=9, frameon=False)
    fig.tight_layout()
    fig.savefig(destination, dpi=240, facecolor='white')
    plt.close(fig)


def verify_pdf(pdf: Path, build_status: Path) -> dict:
    import pymupdf
    status = json.loads(build_status.read_text(encoding='utf8'))
    if status['render_mode'] != 'final' or status['main_results_ready'] is not True:
        raise ValueError('A real completed-study report is required')
    with pymupdf.open(pdf) as document:
        texts = [page.get_text() for page in document]
    joined = '\n'.join(texts)
    if any(marker.lower() in joined.lower() for marker in ('TEMP FIXTURE', 'withheld pending', 'SYNTHETIC LAYOUT QA')):
        raise ValueError('Report contains a draft or synthetic-fixture marker')
    ref_page = next((i for i, text in enumerate(texts) if re.search(r'^References\s*$', text, re.M)), None)
    if ref_page is None or ref_page > 8:
        raise ValueError('Report must include references after at most eight main-text pages')
    abstract = re.split(r'\babstract\b', joined, maxsplit=1, flags=re.I)[1]
    abstract = re.split(r'\b(?:keywords|introduction)\b', abstract, maxsplit=1, flags=re.I)[0]
    abstract = re.sub(r'-[ \t]*\n[ \t]*', '-', abstract)
    words = len(re.findall(r"\b[\w]+(?:[-'][\w]+)*\b", abstract))
    if not 150 <= words <= 250 or pdf.stat().st_size >= 25 * 1024**2:
        raise ValueError('Report abstract or PDF size is outside the release bounds')
    return {'pdf': pdf.name, 'sha256': digest(pdf), 'pages': len(texts), 'main_pages_before_references': ref_page,
            'abstract_words_pdf': words, 'bytes': pdf.stat().st_size}


def generate_extension_manifest(output: Path) -> dict:
    """Generate extension manifest covering production modules and test suites."""
    manifest_path = output / 'seal/extension_source_manifest.json'
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_contract = json.loads((DATA / 'provenance/source_contract.json').read_text(encoding='utf8'))
    extension_files = sorted({path.relative_to(ROOT).as_posix()
        for directory in ('src', 'scripts', 'tests') for path in (ROOT / directory).rglob('*.py')
        if path.relative_to(ROOT).as_posix() not in legacy_contract['sha256']
        and '__pycache__' not in path.parts})
    extension_hashes = {}
    for rel in extension_files:
        p = ROOT / rel
        if not p.is_file():
            raise ValueError(f'Extension file missing: {rel}')
        extension_hashes[rel] = digest(p)

    legacy_manifest_path = ROOT / 'legacy_sources/source_manifest.json'
    if not legacy_manifest_path.is_file():
        raise ValueError(f'Missing legacy manifest: {legacy_manifest_path}')
    legacy_manifest = json.loads(legacy_manifest_path.read_text(encoding='utf8'))
    for k, v in legacy_contract['sha256'].items():
        if legacy_manifest.get(k) != v:
            raise ValueError(f'Legacy manifest inconsistency for {k}: expected {v}, got {legacy_manifest.get(k)}')

    manifest = {
        'schema_version': 'seal-extension-manifest-v1',
        'purpose': 'All Python source, scripts, and tests outside the five-module frozen contract',
        'extension_sources': extension_hashes,
        'legacy_sources_pinned': legacy_contract['sha256'],
        'legacy_manifest_verified': True,
        'all_extension_files_verified': True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf8')
    return manifest


def reproduce(output: Path, *, analysis_only: bool = False, legacy: bool = False) -> dict:
    output = output.resolve()
    if output == ROOT or output.is_relative_to(DATA.resolve()) or DATA.resolve().is_relative_to(output):
        raise ValueError('Build output must be separate from source and immutable study inputs')
    output.mkdir(parents=True, exist_ok=True)

    if legacy:
        # Isolated legacy reproduction using preserved legacy sources
        legacy_dir = (ROOT / 'legacy_sources').resolve()
        verification = verify_inputs(source_root=legacy_dir, legacy_only=True)
        verification.update(verify_recovery_inputs())
        responses = materialize_study(DATA, output)

        saved_sys_path = list(sys.path)
        saved_source_modules = {
            name: module for name, module in sys.modules.items()
            if name == 'src' or name.startswith('src.')
        }
        try:
            sys.path = [str(legacy_dir)] + [
                p for p in sys.path
                if Path(p).resolve() not in (ROOT.resolve(), (ROOT / 'src').resolve())
            ]
            for mod_name in list(sys.modules.keys()):
                if mod_name == 'src' or mod_name.startswith('src.'):
                    del sys.modules[mod_name]

            from src import live_study
            from src.live_analysis import run_analysis

            # Assert isolated imports resolved strictly to legacy_sources
            assert Path(live_study.__file__).resolve().is_relative_to(legacy_dir), (
                f'Isolated legacy import violation: live_study resolved to {live_study.__file__}'
            )
            assert Path(run_analysis.__code__.co_filename).resolve().is_relative_to(legacy_dir), (
                f'Isolated legacy import violation: live_analysis resolved to {run_analysis.__code__.co_filename}'
            )

            live_study.configure_study_paths(output / 'study/main')
            live_study.export_analysis_jsonl(output / 'episodes.jsonl')
            run_analysis(output / 'episodes.jsonl', output / 'analysis', bootstrap_replicates=2000, seed=20261108, ci=.95)
        finally:
            sys.path = saved_sys_path
            for name in list(sys.modules):
                if name == 'src' or name.startswith('src.'):
                    del sys.modules[name]
            sys.modules.update(saved_source_modules)

        verification.update(verify_analysis(output))
        verification.update(audit_execution(responses, output))
        render_overview(output / 'analysis', output / 'overview.png')
        verification['mode'] = 'legacy'
        (output / 'reproduction.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf8')
        return verification

    # Default reproduction: verifies current sources, runs frozen analysis, then SEAL extensions
    verification = verify_inputs(source_root=ROOT, legacy_only=False)
    verification.update(verify_recovery_inputs())
    responses = materialize_study(DATA, output)

    from src import live_study
    from src.live_analysis import run_analysis
    from src.seal_analysis import run_seal_stages_1_3
    from scripts.mutate_gate import generate_gate_mutations

    live_study.configure_study_paths(output / 'study/main')
    live_study.export_analysis_jsonl(output / 'episodes.jsonl')
    run_analysis(output / 'episodes.jsonl', output / 'analysis', bootstrap_replicates=2000, seed=20261108, ci=.95)
    verification.update(verify_analysis(output))
    verification.update(audit_execution(responses, output))
    render_overview(output / 'analysis', output / 'overview.png')

    # SEAL Retrospective Extensions: Stages 1, 2, and 3
    seal_analysis_dir = output / 'seal/analysis'
    seal_summary = run_seal_stages_1_3(output / 'episodes.jsonl', seal_analysis_dir)

    # Gate mutation sweep (Stage 2)
    gate_csv = output / 'gate_mutations.csv'
    gate_manifest_path = output / 'gate_mutations_manifest.json'
    gate_manifest = generate_gate_mutations(gate_csv, gate_manifest_path)

    # Mirror Stage 2 under build/seal/
    seal_gate_csv = output / 'seal/gate_mutations.csv'
    seal_gate_manifest = output / 'seal/gate_mutations_manifest.json'
    shutil.copyfile(gate_csv, seal_gate_csv)
    shutil.copyfile(gate_manifest_path, seal_gate_manifest)

    # Extension manifest covering production and tests
    ext_manifest = generate_extension_manifest(output)

    verification['seal'] = {
        'schema_version': seal_summary['schema_version'],
        'stages_1_3_outputs': seal_summary['outputs'],
        'stage_2_total_mutations': gate_manifest['total_mutations_evaluated'],
        'stage_2_observed_actions': gate_manifest['observed_gate_actions'],
        'stage_2_unsafe_executions': gate_manifest['attack_unsafe_executions'],
        'positive_controls_passed': gate_manifest['controls']['positive_legitimate_controls']['all_passed'],
        'mismatch_controls_passed': gate_manifest['controls']['isolated_equality_mismatch_controls']['all_passed'],
        'extension_manifest': 'seal/extension_source_manifest.json',
    }

    if not analysis_only:
        from scripts.prepare_live_paper import run
        report = output / 'paper'
        run(analysis_dir=output / 'analysis', study_dir=output / 'study/main', worker_dir=DATA / 'workers',
            output_dir=report, temp_fixture=False, compile_pdf_flag=True, force_final=True)
        verification['report'] = verify_pdf(report / 'main.pdf', report / 'build_status.json')
    verification['mode'] = 'analysis_only' if analysis_only else 'complete'
    (output / 'reproduction.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf8')
    return verification


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build', help='Generated outputs, default: build/')
    parser.add_argument('--analysis-only', action='store_true', help='Rebuild numeric evidence without requiring TeX')
    parser.add_argument('--verify-only', action='store_true', help='Verify frozen inputs and sources without rebuilding')
    parser.add_argument('--legacy', action='store_true', help='Run isolated legacy reproduction using preserved legacy sources')
    parser.add_argument('--update-docs-figure', action='store_true', help='Refresh the versioned README figure from rebuilt results')
    args = parser.parse_args()
    if args.verify_only:
        source_root = (ROOT / 'legacy_sources') if args.legacy else ROOT
        result = {**verify_inputs(source_root=source_root, legacy_only=args.legacy), **verify_recovery_inputs()}
    else:
        result = reproduce(args.output, analysis_only=args.analysis_only, legacy=args.legacy)
    if args.update_docs_figure:
        if args.verify_only:
            parser.error('--update-docs-figure requires rebuilding the analysis')
        shutil.copyfile(args.output / 'overview.png', ROOT / 'docs/assets/oversight-results.png')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
