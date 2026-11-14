import json
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_seal_admit.py"
OUTPUT_DIR = ROOT / "build" / "venues" / "admit"
PAPER_DIR = ROOT / "paper" / "venues" / "admit"


def tectonic_available() -> bool:
    import shutil

    if shutil.which("tectonic"):
        return True
    for candidate in (
        ROOT / ".tools" / "tectonic" / "tectonic.exe",
        ROOT / ".tools" / "tectonic" / "tectonic",
        ROOT / ".tools" / "tectonic.exe",
    ):
        if candidate.is_file():
            return True
    return False


class SealAdmitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.output_dir = Path(cls.scratch.name) / "build"
        cls.paper_dir = Path(cls.scratch.name) / "paper"
        cmd = [sys.executable, str(SCRIPT), "--compile", "--output-dir", str(cls.output_dir), "--paper-dir", str(cls.paper_dir)]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"prepare_seal_admit failed:\n{proc.stdout}\n{proc.stderr}")

    def test_output_files_exist(self):
        self.assertTrue((self.output_dir / "admit.pdf").is_file())
        self.assertTrue((self.paper_dir / "admit.tex").is_file())
        self.assertTrue((self.output_dir / "generated.tex").is_file())
        self.assertTrue((self.output_dir / "evidence_manifest.json").is_file())
        self.assertTrue((self.output_dir / "claim_to_evidence.json").is_file())
        for name in ('generated.tex', 'authors.tex', 'abstract.tex', 'table_endpoints.tex'):
            self.assertTrue((self.paper_dir / 'live_generated' / name).is_file())

    def test_no_appendix_or_a3(self):
        for directory in (self.output_dir, self.paper_dir):
            for path in directory.iterdir():
                self.assertFalse(path.name.lower().startswith("appendix"))
                self.assertFalse(path.name.lower().startswith("a3"))
        main_tex = (self.paper_dir / "admit.tex").read_text(encoding="utf8")
        self.assertNotIn("\\appendix", main_tex)
        self.assertNotIn("appendix", main_tex.lower())

    def test_author_name_matches_authors_json(self):
        authors = json.loads((ROOT / "paper" / "authors.json").read_text(encoding="utf8"))
        expected = authors["authors"][0]["name"]
        main_tex = (self.paper_dir / "admit.tex").read_text(encoding="utf8")
        generated = (self.output_dir / "generated.tex").read_text(encoding="utf8")
        self.assertIn(expected.split()[0], main_tex + generated)

    def test_numeric_macros_from_artifacts(self):
        generated = (self.output_dir / "generated.tex").read_text(encoding="utf8")
        self.assertIn("\\newcommand{\\AttackScheduled}{640}", generated)
        self.assertIn("\\newcommand{\\ProvenanceUnsafeExecutions}{0}", generated)
        self.assertIn("\\newcommand{\\MutationsTotal}{400}", generated)
        self.assertIn("\\newcommand{\\PositiveControlsPassed}{40}", generated)

    def test_blind_excluded(self):
        main_tex = (self.paper_dir / "admit.tex").read_text(encoding="utf8")
        generated = (self.output_dir / "generated.tex").read_text(encoding="utf8")
        combined = main_tex + generated
        self.assertNotIn("blind provenance rate", combined.lower())
        manifest = json.loads((self.output_dir / "evidence_manifest.json").read_text(encoding="utf8"))
        self.assertFalse(manifest["blind_included"])
        self.assertFalse(manifest["appendix_included"])

    def test_unverified_blind_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--include-blind",
                    "--blind-dir",
                    tmpdir,
                    "--output-dir",
                    str(Path(tmpdir) / "out"),
                    "--paper-dir",
                    str(Path(tmpdir) / "paper"),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)

    def test_pdf_page_count_one(self):
        if not tectonic_available():
            self.skipTest("tectonic unavailable")
        # Use pdftotext? fallback: check file size only; page count via PyPDF if available.
        try:
            from pypdf import PdfReader
        except Exception:
            try:
                from PyPDF2 import PdfReader
            except Exception:
                self.skipTest("pypdf unavailable")
        reader = PdfReader(str(self.output_dir / "admit.pdf"))
        self.assertEqual(len(reader.pages), 1)


if __name__ == "__main__":
    unittest.main()
