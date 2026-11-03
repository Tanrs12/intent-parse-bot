"""Execute the real browser theme-control script against restart and sharing cases."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_theme_choice_survives_reopen_and_syncs_windows():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "tests/theme_persistence_smoke.js"],
        cwd=root, capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "PASS:" in result.stdout
