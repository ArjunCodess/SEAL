"""Offline source-portability regression for the four SEAL paper deliverables.

Each delivered source set (paper/main.tex with arxiv.sty and the figure,
paper/venues/{agentverify,admit,aaai}) is copied into a disposable temporary
directory and compiled there with the venue's engine, proving it compiles
independently of build/ paths. Assertions cover page caps, unresolved
references, and venue anonymity requirements.

Read-only for paper/, build/, and frozen study data; no model, API, or
network calls. Compilation is skipped when the engine is unavailable.
"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
AAAI_CONTENT_PAGES = 7
AGENTVERIFY_PAGES = 2


def resolve_tectonic() -> str | None:
    installed = shutil.which("tectonic")
    if installed:
        return installed
    for candidate in (
        ROOT / ".tools" / "tectonic" / "tectonic.exe",
        ROOT / ".tools" / "tectonic" / "tectonic",
        ROOT / ".tools" / "tectonic.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_pdflatex() -> str | None:
    return shutil.which("pdflatex")


def pdf_text(pdf_path: Path):
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover - fallback name
        import fitz
    document = fitz.open(pdf_path)
    pages = [page.get_text() for page in document]
    metadata = document.metadata or {}
    document.close()
    return len(pages), "\n".join(pages), metadata


def pdf_page_texts(pdf_path: Path) -> list[str]:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover - fallback name
        import fitz
    document = fitz.open(pdf_path)
    pages = [page.get_text() for page in document]
    document.close()
    return pages


def assert_no_build_paths(sources: list[Path]) -> None:
    for source in sources:
        text = source.read_text(encoding="utf8")
        assert not re.search(r"\\input\{.*build[/\\]|includegraphics.*build[/\\]", text), source


def run_pdflatex(work: Path, name: str) -> Path:
    out = work / "out"
    out.mkdir()
    for _ in range(2):  # two passes resolve cross-references
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             f"-output-directory={out}", f"{name}.tex"],
            cwd=work, capture_output=True, text=True, errors="replace", timeout=300,
        )
        assert result.returncode == 0, result.stdout[-4000:]
    pdf = out / f"{name}.pdf"
    assert pdf.is_file()
    return pdf


def run_tectonic(work: Path, name: str, tectonic: str) -> Path:
    out = work / "out"
    out.mkdir()
    result = subprocess.run(
        [tectonic, f"{name}.tex", "--outdir", str(out)],
        cwd=work, capture_output=True, text=True, errors="replace", timeout=300,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    pdf = out / f"{name}.pdf"
    assert pdf.is_file()
    return pdf


def stage(work: Path, files: list[Path]) -> list[Path]:
    staged = []
    for source in files:
        target = work / source.name
        shutil.copy2(source, target)
        staged.append(target)
    return staged


def test_main_preprint_compiles_standalone():
    pdflatex = resolve_pdflatex()
    if not pdflatex:
        pytest.skip("pdflatex unavailable")
    files = [PAPER / "main.tex", PAPER / "arxiv.sty", PAPER / "seal_measurement.pdf"]
    assert_no_build_paths(files[:1])
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        stage(work, files)
        pdf = run_pdflatex(work, "main")
        pages, text, _ = pdf_text(pdf)
        assert pages >= 1
        assert "??" not in text  # no unresolved references
        assert "Arjun Vijay Prakash" in text  # author retained in preprint


def test_aaai_compiles_standalone_and_within_content_cap():
    pdflatex = resolve_pdflatex()
    if not pdflatex:
        pytest.skip("pdflatex unavailable")
    files = [PAPER / "venues" / "aaai" / name
             for name in ("aaai.tex", "aaai2027.sty", "aaai2027.bst")]
    assert_no_build_paths(files[:1])
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        stage(work, files)
        pdf = run_pdflatex(work, "aaai")
        pages, text, _ = pdf_text(pdf)
        per_page = pdf_page_texts(pdf)
        reference_page = next(
            (i for i, page in enumerate(per_page) if "References" in page), len(per_page))
        before = per_page[reference_page].split("References", 1)[0].strip() if reference_page < len(per_page) else ""
        body_pages = reference_page + bool(before)  # same rule as prepare_seal_paper.py
        assert body_pages <= AAAI_CONTENT_PAGES
        assert "??" not in text
        assert "Arjun Vijay Prakash" in text  # AAAI review is single-blind: author retained


def test_agentverify_compiles_standalone_and_anonymous():
    tectonic = resolve_tectonic()
    if not tectonic:
        pytest.skip("tectonic unavailable")
    folder = PAPER / "venues" / "agentverify"
    files = [folder / "agentverify.tex", folder / "IEEEtran.cls"]
    files += sorted((folder / "generated").glob("*.tex"))
    assert_no_build_paths(files[:1])
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        stage(work, files)
        generated = work / "generated"
        generated.mkdir()
        for source in sorted((folder / "generated").glob("*.tex")):
            shutil.copy2(source, generated / source.name)
        pdf = run_tectonic(work, "agentverify", tectonic)
        pages, text, metadata = pdf_text(pdf)
        assert pages <= AGENTVERIFY_PAGES  # CFP cap, references included
        assert "??" not in text
        lowered = text.lower()
        for forbidden in ("arjun", "prakash", "independent researcher", "github.com"):
            assert forbidden not in lowered
        assert "anonymous" in lowered
        assert not metadata.get("author") or "anonymous" in str(metadata["author"]).lower()


def test_admit_compiles_standalone_one_page():
    tectonic = resolve_tectonic()
    if not tectonic:
        pytest.skip("tectonic unavailable")
    folder = PAPER / "venues" / "admit"
    files = [folder / "admit.tex"]
    files += sorted((folder / "live_generated").glob("*.tex"))
    assert_no_build_paths(files[:1])
    with tempfile.TemporaryDirectory() as tmpdir:
        work = Path(tmpdir)
        stage(work, files)
        live = work / "live_generated"
        live.mkdir()
        for source in sorted((folder / "live_generated").glob("*.tex")):
            shutil.copy2(source, live / source.name)
        pdf = run_tectonic(work, "admit", tectonic)
        pages, text, _ = pdf_text(pdf)
        assert pages == 1  # one-page presentation proposal
        assert "??" not in text
        assert "Arjun Vijay Prakash" in text  # author retained
