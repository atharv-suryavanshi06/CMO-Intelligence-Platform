"""Multimodal RAG application namespace package.

The implementation is organized into component source roots while retaining
the established ``multimodal_rag.*`` import contract.
"""

from pkgutil import extend_path
from pathlib import Path

__path__ = extend_path(__path__, __name__)

# Editable installs expose the component source roots through separate package
# mappings. Extend the namespace explicitly so regular imports work before a
# wheel is built as well as after installation.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
for _source in (
    _PROJECT_ROOT / "packages" / "agents" / "src" / "multimodal_rag",
    _PROJECT_ROOT / "packages" / "web-search" / "src" / "multimodal_rag",
    _PROJECT_ROOT / "packages" / "ingestion" / "src" / "multimodal_rag",
    _PROJECT_ROOT / "packages" / "security" / "src" / "multimodal_rag",
    _PROJECT_ROOT / "services" / "api" / "src" / "multimodal_rag",
):
    if _source.is_dir() and str(_source) not in __path__:
        __path__.append(str(_source))
