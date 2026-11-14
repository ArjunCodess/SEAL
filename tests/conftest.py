"""Test isolation for generated release artifacts.

Several offline collection tests exercise ``seal_collection.collect`` with an
injected client.  That code path regenerates the shared budget status, which
must never overwrite the real release artifact from a pytest temporary study
directory.  Redirect the build output for every test; no release artifact under
``build/seal`` is written by the suite.
"""
from __future__ import annotations

import pytest

from src import seal_collection as sc


@pytest.fixture(autouse=True)
def isolate_seal_build_outputs(tmp_path, monkeypatch):
    build_dir = tmp_path / "build" / "seal"
    monkeypatch.setattr(sc, "BUILD_DIR", build_dir)
    monkeypatch.setattr(sc, "BUDGET_STATUS_PATH", build_dir / "budget_status.json")
