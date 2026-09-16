"""Start the RAG API with persistent-account or legacy-token authentication."""

from __future__ import annotations

import os
import sys
from getpass import getpass
from pathlib import Path

import uvicorn


def _ensure_source_roots() -> None:
    """Make the component source roots importable without an editable install.

    ``py run_backend.py`` can select a system interpreter instead of the
    project's virtual environment.  Uvicorn imports the application from its
    string target after the launcher starts, so the namespace-package roots
    must be present on ``sys.path`` before that import occurs.
    """

    project_root = Path(__file__).resolve().parents[2]
    source_roots = (
        project_root / "packages" / "rag-core" / "src",
        project_root / "packages" / "ingestion" / "src",
        project_root / "packages" / "agents" / "src",
        project_root / "packages" / "web-search" / "src",
        project_root / "packages" / "security" / "src",
        project_root / "services" / "api" / "src",
    )
    for source_root in reversed(source_roots):
        source_root_text = str(source_root)
        if source_root.is_dir() and source_root_text not in sys.path:
            sys.path.insert(0, source_root_text)


def main() -> None:
    if not (os.getenv("RAG_DATABASE_URL") or os.getenv("RAG_MEMORY_DATABASE_URL")):
        token = getpass("Enter RAG API token: ").strip()
        if not token:
            raise SystemExit("A non-empty RAG API token is required when persistent accounts are not configured.")
        # Preserve the compatibility path for deployments without PostgreSQL.
        os.environ["RAG_API_AUTH_TOKEN"] = token
    _ensure_source_roots()
    uvicorn.run(
        "multimodal_rag.api.main:app",
        host="127.0.0.1",
        port=8000,
    )


if __name__ == "__main__":
    main()
