"""Compatibility launcher for the canonical API service entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _relaunch_in_project_venv() -> None:
    """Use the project's dependency environment when global Python launched us."""
    project_root = Path(__file__).resolve().parent
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        raise SystemExit(subprocess.call([str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]]))


_relaunch_in_project_venv()

from services.api.run_backend import main


if __name__ == "__main__":
    main()
