"""Compatibility namespace for legacy source-path launches."""

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
_ROOT = Path(__file__).resolve().parents[2]
for _source in (
    _ROOT / "packages" / "rag-core" / "src" / "multimodal_rag",
    _ROOT / "packages" / "agents" / "src" / "multimodal_rag",
    _ROOT / "packages" / "web-search" / "src" / "multimodal_rag",
    _ROOT / "packages" / "ingestion" / "src" / "multimodal_rag",
    _ROOT / "packages" / "security" / "src" / "multimodal_rag",
    _ROOT / "services" / "api" / "src" / "multimodal_rag",
):
    if _source.is_dir() and str(_source) not in __path__:
        __path__.append(str(_source))
