"""
Human-Readable Extraction Renderer
=====================================

Produces ONE new, additive file per document -
`<output_dir>/<document_id>/human_readable_extraction.md` - that renders
the full pipeline output as a readable document reconstruction, meant to
be opened side-by-side with the original PDF for quick visual auditing.

This is DELIBERATELY separate from Module 9's `extracted_text_audit.md`:
that file is a technical, per-region log (status markers, extraction
method, confidence, failure reasons) aimed at debugging the pipeline
itself. This file is aimed at a human comparing the RECONSTRUCTED
DOCUMENT against the source PDF - closer to "does this read like the
original" than "what did the pipeline do to each region."

This module does not modify, read from, or write to any of Module 9's
existing output files. It consumes the same `ordered_pairs` data the
orchestrator already builds for chunking/output-writing, purely as an
additional read-only consumer.
"""

from __future__ import annotations

from pathlib import Path

from multimodal_rag.ingestion.analysis.layout_segmenter import Region
from multimodal_rag.ingestion.processing.validator import ValidatedRegionResult

_FALLBACK_LABELS = frozenset({"native_text_fallback", "native_text_supplement", "pdfplumber_table_fallback"})


def _heading_prefix(region: Region) -> str:
    # Docling's raw_label distinguishes "title" from "section_header"
    # even though both map to our internal region_type="heading" - reused
    # here (not a new field) to give the rendered document real heading
    # hierarchy instead of flattening every heading to the same level.
    return "#" if region.raw_label == "title" else "##"


def _render_region(region: Region, validated: ValidatedRegionResult) -> str:
    recovered_note = " _(recovered via fallback - see raw/ for what Docling itself produced)_" \
        if region.raw_label in _FALLBACK_LABELS else ""

    if validated.validation_status == "failed" or not (validated.final_text or "").strip():
        if region.region_type == "figure" and validated.notes and "decorative" in " ".join(validated.notes).lower():
            return ""  # decorative images are intentionally invisible here too, not a gap to flag
        return f"> ⚠️ *[Content missing or unrecoverable here - page {validated.page_number}, " \
               f"region type: {region.region_type}]*\n"

    text = validated.final_text.strip()

    if region.region_type == "heading":
        return f"{_heading_prefix(region)} {text}{recovered_note}\n"
    if region.region_type == "list_item":
        # Preserve simple bullet structure line-by-line in case the
        # recovered text itself contains multiple lines.
        bullets = "\n".join(f"- {line.strip()}" for line in text.split("\n") if line.strip())
        return f"{bullets}{recovered_note}\n"
    if region.region_type == "table":
        table_markdown = validated.table_data.markdown if validated.table_data else text
        return f"{table_markdown}{recovered_note}\n"
    if region.region_type == "caption":
        return f"*{text}*{recovered_note}\n"
    if region.region_type == "figure":
        return f"**[Figure]** {text}{recovered_note}\n"
    if region.region_type in ("header", "footer"):
        return f"<sub>{text}</sub>{recovered_note}\n"
    # text, footnote, formula, code, unknown -> plain paragraph
    return f"{text}{recovered_note}\n"


def _classify_pages(
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]], total_pages: int,
) -> tuple[list[int], list[int], list[int]]:
    """Returns (successfully_extracted, recovered_via_fallback, still_incomplete)."""
    pages_seen: dict[int, list[tuple[Region, ValidatedRegionResult]]] = {}
    for region, validated in ordered_pairs:
        pages_seen.setdefault(validated.page_number, []).append((region, validated))

    successfully_extracted, recovered, incomplete = [], [], []
    for page_num in range(1, total_pages + 1):
        pairs = pages_seen.get(page_num, [])
        has_fallback = any(r.raw_label in _FALLBACK_LABELS for r, _ in pairs)
        has_failed = any(v.validation_status == "failed" for _, v in pairs)

        if not pairs:
            continue  # legitimately blank page - not counted in any bucket, not a problem
        if has_failed:
            incomplete.append(page_num)
        elif has_fallback:
            recovered.append(page_num)
        else:
            successfully_extracted.append(page_num)

    return successfully_extracted, recovered, incomplete


def _render_summary(
    successfully_extracted: list[int], recovered: list[int], incomplete: list[int], total_pages: int,
) -> str:
    if not recovered and not incomplete:
        return f"**Extraction summary:** all {total_pages} pages extracted successfully, no fallback needed.\n\n---\n\n"

    def _fmt(pages: list[int]) -> str:
        return ", ".join(str(p) for p in pages) if pages else "none"

    return (
        "## Extraction Coverage Summary\n\n"
        f"- **Pages successfully extracted (no fallback needed):** {_fmt(successfully_extracted)}\n"
        f"- **Pages recovered via fallback (PyMuPDF native-text and/or pdfplumber table):** {_fmt(recovered)}\n"
        f"- **Pages still incomplete (content could not be fully recovered):** {_fmt(incomplete)}\n\n"
        "See `raw/` for exactly what Docling itself produced before any fallback, and "
        "`validation_report.json` for the per-region reasons behind any incomplete page.\n\n"
        "---\n\n"
    )


def write_human_readable_extraction(
    document_id: str,
    source_file: str,
    output_dir: str | Path,
    ordered_pairs: list[tuple[Region, ValidatedRegionResult]],
    total_pages: int,
) -> Path:
    """
    Write `<output_dir>/<document_id>/human_readable_extraction.md`.
    Additive only - never touches any of Module 9's existing output files.
    """
    successfully_extracted, recovered, incomplete = _classify_pages(ordered_pairs, total_pages)

    lines = [
        f"# Extraction: {source_file}",
        "",
        _render_summary(successfully_extracted, recovered, incomplete, total_pages),
    ]

    current_page: int | None = None
    for region, validated in ordered_pairs:
        if validated.page_number != current_page:
            current_page = validated.page_number
            lines.append(f"\n---\n### 📄 Page {current_page}\n")
        rendered = _render_region(region, validated)
        if rendered:
            lines.append(rendered)

    out_path = Path(output_dir) / document_id / "human_readable_extraction.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
    "\n".join(lines),
    encoding="utf-8")
    return out_path
