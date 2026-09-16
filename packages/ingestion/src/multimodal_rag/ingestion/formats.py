"""File-format policy shared by ingestion entry points.

The allowlist is intentionally broader than the currently active worker so
future media and Word-document pipelines can be added without changing the
accepted-format contract exposed by the UI and API.
"""

from __future__ import annotations

from pathlib import Path

SUPPORTED_INGESTION_EXTENSIONS = frozenset({".pdf", ".mp4", ".mp3", ".doc", ".docx", ".ppt", ".pptx"})
ACTIVE_INGESTION_EXTENSIONS = frozenset({".pdf", ".mp3", ".mp4", ".doc", ".docx", ".ppt", ".pptx"})
SUPPORTED_INGESTION_FORMAT_LABEL = "PDF, MP4, MP3, DOC, DOCX, PPT, PPTX"


def file_extension(filename: str | Path) -> str:
    """Return a normalized lowercase extension, including the leading dot."""
    return Path(filename).suffix.lower()


def is_supported_ingestion_file(filename: str | Path) -> bool:
    """Return whether a filename belongs to the public ingestion allowlist."""
    return file_extension(filename) in SUPPORTED_INGESTION_EXTENSIONS


def is_active_ingestion_file(filename: str | Path) -> bool:
    """Return whether a filename has an implemented ingestion worker."""
    return file_extension(filename) in ACTIVE_INGESTION_EXTENSIONS
