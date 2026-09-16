"""
Layout Analysis Module
=========================

New stage sitting between LayoutSegmenter and RoutingPolicy:

    LayoutSegmenter -> list[Region] -> LayoutAnalysisBuilder -> dict[int, LayoutAnalysis]

Turns the flat `Region` list Module 3 (layout_segmenter.py) already
produces into per-page structural MEASUREMENTS - region counts, text
fragmentation, bounding-box alignment, and a composite infographic
score. This module answers "what does this page's layout look like?",
never "what should we do about it?" - that question belongs entirely
to RoutingPolicy (a separate, not-yet-built module), which is free to
consume these measurements alongside PageAnalysis (Module 2's output)
and apply its own thresholds. Keeping that boundary here is what lets
RoutingPolicy's cutoffs change, or a second policy (e.g. a future
TableRecoveryPolicy) get added, without touching this file at all.

Design notes (consistent with layout_segmenter.py / page_preanalyzer.py):
- `Region` (layout_segmenter.py) is READ ONLY here, never modified or
  subclassed - this module has no reason to touch it, and doesn't.
- Every computation is a pure function of an already-built `list[Region]`
  plus a `LayoutAnalysisConfig` - no PDF access, no model calls, no I/O.
  Same testability shape as `_map_docling_document` in
  layout_segmenter.py: unit-testable against hand-built Region fixtures,
  independent of Docling actually running.
- `region_type_counts` is deliberately included even though only
  text/figure/table counts were asked for by name: it's the same
  per-type breakdown generalized, so a future routing decision that
  cares about, say, `list_item` density (checklists) or `formula`
  density doesn't require a new field or a new builder function - it's
  already sitting in the dict.
- All page-level scores are FLOATS in [0.0, 1.0], never booleans or
  thresholded decisions - see module docstring above.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from multimodal_rag.ingestion.analysis.layout_segmenter import Region

logger = logging.getLogger(__name__)


@dataclass
class LayoutAnalysisConfig:
    short_text_threshold_chars: int = 40
    # A text-bearing region at or below this length counts as "short"
    # for fragmentation purposes - short labels/checklist items/matrix
    # cells, as opposed to paragraph-length text. Deliberately a simple
    # fixed cutoff (not a percentile or corpus-relative measure) so this
    # stays a pure, single-page function with no cross-document state.

    alignment_tolerance_pt: float = 3.0
    # Two regions are considered aligned on an edge (left/top) if their
    # bbox coordinates differ by no more than this many PDF points.
    # Matches the coordinate space Region.bbox is already stored in
    # (Docling's coord space - see Region's docstring), so no unit
    # conversion is needed here.

    fragmentation_weight: float = 0.5
    alignment_weight: float = 0.5
    # Weights combining fragmentation_score and alignment_score into
    # infographic_score. Kept as two independent, renamed weights
    # (rather than a single "mix ratio") so a future third signal can
    # be added to the composite without restructuring this config -
    # just add a new weight field and include it in
    # _compute_infographic_score's weighted sum.


@dataclass
class LayoutAnalysis:
    """
    Pure, per-page structural measurements derived from a page's
    Region objects. Contains no decisions and no thresholds - see
    module docstring. RoutingPolicy (separate module) is the intended
    consumer.
    """
    page_number: int
    region_count: int
    text_region_count: int
    figure_count: int
    table_count: int
    region_type_counts: dict[str, int]
    average_text_length: float
    fragmentation_score: float
    alignment_score: float
    infographic_score: float


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------

def _group_by_page(regions: list[Region]) -> dict[int, list[Region]]:
    """Group regions by page_number, preserving each page's existing
    relative ordering (regions arrive pre-sorted by the orchestrator,
    but this function doesn't assume or require that)."""
    pages: dict[int, list[Region]] = {}
    for region in regions:
        pages.setdefault(region.page_number, []).append(region)
    return pages


# --------------------------------------------------------------------------
# Per-page measurements
# --------------------------------------------------------------------------

def _compute_region_type_counts(regions: list[Region]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for region in regions:
        counts[region.region_type] = counts.get(region.region_type, 0) + 1
    return counts


def _compute_average_text_length(regions: list[Region]) -> float:
    """Mean character length of text_content across regions that carry
    text (tables/figures typically don't, and are excluded rather than
    counted as zero-length - a zero would incorrectly pull the average
    down for pages with many non-text regions)."""
    lengths = [len(r.text_content) for r in regions if r.text_content]
    if not lengths:
        return 0.0
    return sum(lengths) / len(lengths)


def _compute_fragmentation_score(regions: list[Region], config: LayoutAnalysisConfig) -> float:
    """
    Fraction of text-bearing regions whose text is "short" (see
    LayoutAnalysisConfig.short_text_threshold_chars). Infographics -
    checklists, comparison matrices, SmartArt-like layouts - tend to
    decompose into many short, independent text regions (labels, cells,
    bullet phrases) rather than the few long regions a paragraph-flow
    page produces. Pages with no text-bearing regions score 0.0 (no
    text to fragment, not maximally fragmented).
    """
    text_regions = [r for r in regions if r.text_content]
    if not text_regions:
        return 0.0
    short_count = sum(1 for r in text_regions if len(r.text_content) <= config.short_text_threshold_chars)
    return short_count / len(text_regions)


def _cluster_edges(edges: list[float], tolerance: float) -> list[list[int]]:
    """
    Greedily group edge coordinates that fall within `tolerance` of
    their neighbor, sorted by value. Returns clusters as lists of
    indices into the original `edges` list.

    KNOWN LIMITATION (disclosed, not hidden): this is a simple
    chained/greedy grouping (a run of values each within tolerance of
    the previous one is one cluster), so a dense run of many regions
    could in principle chain-drift beyond `tolerance` end-to-end. Good
    enough for the coarse grid-vs-not-grid signal this module needs;
    not a substitute for a real clustering algorithm if finer-grained
    column/row detection is ever required.
    """
    if not edges:
        return []
    order = sorted(range(len(edges)), key=lambda i: edges[i])
    clusters: list[list[int]] = []
    current: list[int] = [order[0]]
    for idx in order[1:]:
        if abs(edges[idx] - edges[current[-1]]) <= tolerance:
            current.append(idx)
        else:
            clusters.append(current)
            current = [idx]
    clusters.append(current)
    return clusters


def _compute_alignment_score(regions: list[Region], config: LayoutAnalysisConfig) -> float:
    """
    Detects genuine GRID structure - multiple distinct columns AND
    multiple distinct rows, each shared by 2+ regions - as opposed to
    merely "some regions share an edge," which is true of almost every
    single-column document (every paragraph shares the same left
    margin) and would otherwise make ordinary text pages score as
    aligned. Comparison tables, aligned box layouts, and multi-column
    summaries produce multiple left-edge clusters (columns) crossed
    with multiple top-edge clusters (rows); normal paragraph flow
    produces exactly one column and no repeated row alignment.

    Returns the fraction of regions that belong to both a multi-member
    column cluster AND a multi-member row cluster - i.e. true grid-cell
    membership. Returns 0.0 if fewer than 2 qualifying column clusters
    or fewer than 2 qualifying row clusters exist at all (no grid, by
    definition, regardless of any single shared margin).
    """
    if len(regions) < 2:
        return 0.0

    left_edges = [r.bbox[0] for r in regions]
    top_edges = [r.bbox[1] for r in regions]

    column_clusters = [c for c in _cluster_edges(left_edges, config.alignment_tolerance_pt) if len(c) >= 2]
    row_clusters = [c for c in _cluster_edges(top_edges, config.alignment_tolerance_pt) if len(c) >= 2]

    if len(column_clusters) < 2 or len(row_clusters) < 2:
        return 0.0

    in_column = {i for cluster in column_clusters for i in cluster}
    in_row = {i for cluster in row_clusters for i in cluster}
    grid_cell_members = in_column & in_row

    return len(grid_cell_members) / len(regions)


def _compute_infographic_score(
    fragmentation_score: float, alignment_score: float, config: LayoutAnalysisConfig,
) -> float:
    """
    Composite measurement combining fragmentation and alignment into a
    single page-level score in [0.0, 1.0]. Deliberately a plain
    weighted sum, not a learned/calibrated model - this stays a cheap,
    inspectable, unit-testable function; RoutingPolicy is where any
    cutoff on this score is decided, not here.
    """
    weight_total = config.fragmentation_weight + config.alignment_weight
    if weight_total <= 0:
        return 0.0
    raw = (
        fragmentation_score * config.fragmentation_weight
        + alignment_score * config.alignment_weight
    )
    return raw / weight_total


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def build_layout_analysis(
    regions: list[Region], config: LayoutAnalysisConfig | None = None,
) -> dict[int, LayoutAnalysis]:
    """
    Compute per-page LayoutAnalysis measurements from a document's full
    Region list. Pure function: same inputs always produce the same
    output, no I/O, no mutation of `regions` or any Region within it.

    Pages with zero regions (e.g. legitimately blank pages, already
    handled upstream by the orchestrator's page-coverage check) simply
    do not appear in the returned dict - callers should treat a missing
    page_number as "no layout signal available," the same convention
    already used for `page_analysis_by_number.get(page_num)` in
    orchestrator.py.
    """
    config = config or LayoutAnalysisConfig()
    pages = _group_by_page(regions)

    analyses: dict[int, LayoutAnalysis] = {}
    for page_number, page_regions in pages.items():
        region_type_counts = _compute_region_type_counts(page_regions)
        fragmentation_score = _compute_fragmentation_score(page_regions, config)
        alignment_score = _compute_alignment_score(page_regions, config)

        analyses[page_number] = LayoutAnalysis(
            page_number=page_number,
            region_count=len(page_regions),
            text_region_count=region_type_counts.get("text", 0),
            figure_count=region_type_counts.get("figure", 0),
            table_count=region_type_counts.get("table", 0),
            region_type_counts=region_type_counts,
            average_text_length=_compute_average_text_length(page_regions),
            fragmentation_score=fragmentation_score,
            alignment_score=alignment_score,
            infographic_score=_compute_infographic_score(fragmentation_score, alignment_score, config),
        )

    logger.info("Built layout analysis for %d page(s)", len(analyses))
    return analyses
