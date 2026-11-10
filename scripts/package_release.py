"""Package the research source, frozen evidence and rebuilt report."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import zipfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.reproduce import DATA, digest, verify_inputs, verify_recovery_inputs, verify_analysis, verify_pdf

PREFIX = 'supervisor-evidence-arms/'


def release_files(root: Path, build: Path) -> dict[str, Path]:
    files = {}
    for directory in ('src', 'scripts', 'tests', 'paper', 'data/study', 'experiments', 'docs', '.github'):
        for path in (root / directory).rglob('*'):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix not in {'.pyc', '.log', '.aux', '.out'}:
                files[path.relative_to(root).as_posix()] = path
    for name in ('README.md', 'LICENSE', 'CITATION.cff', '.gitignore', '.gitattributes', 'pyproject.toml', 'requirements.txt', 'run_all.sh', 'run_all.ps1'):
        files[name] = root / name
    output_roots = [(build / name, build) for name in ('analysis', 'paper')]
    output_roots += [(root / 'build' / name, root / 'build') for name in
                     ('recovery-costs', 'recovery-replay-final', 'recovery-study-prep-final', 'natural-context-validated')]
    for directory, base in output_roots:
        for path in directory.rglob('*'):
            if path.name in {'contact-sheet.png', 'final-page.png', 'page-13.png', 'page-14.png'}:
                continue
            if path.is_file() and path.suffix.lower() in {'.csv', '.json', '.md', '.png', '.pdf', '.tex'}:
                files['build/' + path.relative_to(base).as_posix()] = path
    for name in ('reproduction.json', 'gateway_audit.csv', 'overview.png'):
        files['build/' + name] = build / name
    for name, path in files.items():
        if path.suffix.lower() in {'.dpapi', '.pem', '.key'} or path.name.startswith('.env'):
            raise ValueError(f'Credential-like file is not a release artifact: {name}')
    return files


def make_archive(files: dict[str, Path], destination: Path) -> dict:
    temporary = destination.with_suffix('.zip.partial')
    verification = destination.with_suffix('.verification.json')
    inputs = {path.resolve() for path in files.values()}
    for output in (destination, temporary, verification):
        if output.resolve() in inputs:
            raise ValueError(f'Release output would overwrite an input artifact: {output}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    checksums = {name: digest(path) for name, path in sorted(files.items())}
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, PREFIX + name)
        archive.writestr(PREFIX + 'checksums.json', json.dumps(checksums, indent=2) + '\n')
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC check failed')
        for name, expected in checksums.items():
            with archive.open(PREFIX + name) as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError(f'Archive content differs: {name}')
    temporary.replace(destination)
    result = {'archive': destination.name, 'sha256': digest(destination), 'bytes': destination.stat().st_size,
              'files_verified': len(checksums), 'all_hashes_verified': True}
    verification.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, default=ROOT / 'build')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/supervisor-evidence-arms-0.1.0.zip')
    args = parser.parse_args()
    verify_inputs()
    verify_recovery_inputs()
    verify_analysis(args.build)
    verify_pdf(args.build / 'paper/main.pdf', args.build / 'paper/build_status.json')
    print(json.dumps(make_archive(release_files(ROOT, args.build), args.output), indent=2))

if __name__ == '__main__':
    main()
