# -*- coding: utf-8 -*-
"""Regression tests for the local backend gate helper."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CI_GATE = REPO_ROOT / "scripts" / "ci_gate.sh"
DEV_BOOTSTRAP = REPO_ROOT / "scripts" / "dev_bootstrap.sh"


def _run_ci_gate(phase: str, *, path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = str(path)
    return subprocess.run(
        ["/usr/bin/bash", str(CI_GATE), phase],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_ci_gate_explains_missing_flake8(tmp_path: Path) -> None:
    result = _run_ci_gate("flake8", path=tmp_path)

    assert result.returncode == 127
    assert "Missing required development tool: flake8" in result.stderr
    assert "python -m pip install flake8 pytest" in result.stderr


def test_ci_gate_explains_missing_pytest(tmp_path: Path) -> None:
    python = shutil.which("python")
    assert python is not None
    (tmp_path / "python").symlink_to(python)

    result = _run_ci_gate("offline-tests", path=tmp_path)

    assert result.returncode == 127
    assert "Missing required Python module: pytest" in result.stderr
    assert "python -m pip install flake8 pytest" in result.stderr


def test_dev_bootstrap_help_documents_supported_scopes() -> None:
    result = subprocess.run(
        ["/usr/bin/bash", str(DEV_BOOTSTRAP), "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage: scripts/dev_bootstrap.sh [--backend-only|--with-web|--all]" in result.stdout
    assert "Backend dependencies: requirements.txt plus flake8 and pytest" in result.stdout
    assert "Web dependencies: apps/dsa-web/npm ci" in result.stdout
