"""Canonical repository paths for the modular project layout.

This module is intentionally side-effect free: importing it never creates
directories or changes files. Later migration phases will consume these
constants when their respective code and runtime data are moved.
"""

from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PACKAGE_ROOT.parent


def _find_project_root() -> Path:
    """Find the platform root regardless of the component source location."""
    for parent in PACKAGE_ROOT.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("Could not locate the platform pyproject.toml")


PROJECT_ROOT = _find_project_root()

# ``Data`` is a user-provided source directory on Windows. Runtime artifacts
# therefore live in the explicitly named, case-distinct directory below.
DATA_DIR = PROJECT_ROOT / "runtime-data"
INPUT_DIR = DATA_DIR / "input"

# Per-tenant ingestion/index storage (API-scoped uploads, e.g. from the
# frontend) lives outside ``runtime-data`` entirely, in its own top-level
# root. ``runtime-data`` remains the CLI/evaluation-only global corpus and
# log location; it is never read by the per-user retrieval path.
USER_DATA_ROOT_DEFAULT = PROJECT_ROOT / "user-data"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
INGESTION_ARTIFACTS_DIR = ARTIFACTS_DIR / "ingestion"
INDEX_DIR = ARTIFACTS_DIR / "index"
FIGURES_DIR = ARTIFACTS_DIR / "figures"
LEGACY_INGESTION_ARTIFACTS_DIR = ARTIFACTS_DIR / "legacy-ingestion"
COMPARISONS_DIR = DATA_DIR / "comparisons"
MARKER_COMPARISON_DIR = COMPARISONS_DIR / "marker"
UNSTRUCTURED_COMPARISON_DIR = COMPARISONS_DIR / "unstructured"
EVALUATION_ARTIFACTS_DIR = DATA_DIR / "evaluation"
LOGS_DIR = DATA_DIR / "logs"

CONFIG_DIR = PROJECT_ROOT / "config"
EVALUATION_DATASET_DIR = PROJECT_ROOT / "evaluation" / "datasets"
GROUND_TRUTH_PATH = EVALUATION_DATASET_DIR / "ground_truth.json"

# Legacy locations are retained only as temporary read/write fallbacks for
# users who have not moved their local runtime data yet.
LEGACY_INPUT_DIR = PROJECT_ROOT / "input"
LEGACY_OUTPUT_DIR = PROJECT_ROOT / "output"
LEGACY_INDEX_DIR = PROJECT_ROOT / "index"
LEGACY_LOGS_DIR = PROJECT_ROOT / "logs"
LEGACY_INGESTION_IMAGES_DIR = PROJECT_ROOT / "ingestion_output" / "images"
LEGACY_COMPARISONS_DIR = PROJECT_ROOT / "comparison_output"
LEGACY_MARKER_COMPARISON_DIR = LEGACY_COMPARISONS_DIR / "marker_output"
LEGACY_UNSTRUCTURED_COMPARISON_DIR = LEGACY_COMPARISONS_DIR / "unstructured_output"
LEGACY_EVALUATION_DIR = PROJECT_ROOT / "evaluation"
LEGACY_GROUND_TRUTH_PATH = LEGACY_EVALUATION_DIR / "ground_truth.json"


def prefer_new_path(new_path: Path, legacy_path: Path) -> Path:
    """Use the migrated location, falling back to a legacy location only
    when the new location is absent and the legacy location still exists."""
    if new_path.exists() or not legacy_path.exists():
        return new_path
    return legacy_path
