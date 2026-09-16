"""
Routing Policy Module
=========================

New stage sitting after both Module 2 (PagePreAnalyzer) and the
LayoutAnalysisBuilder (layout_analysis.py):

    PageAnalysis + LayoutAnalysis -> RoutingPolicy -> PageRoutingDecision

This is where MEASUREMENTS become a DECISION. Every upstream stage in
this pipeline (PagePreAnalyzer, LayoutSegmenter, LayoutAnalysisBuilder)
deliberately stops short of deciding anything - see layout_analysis.py's
module docstring. This module is the one place that boundary is
crossed, and it is crossed ONLY here: no other module should contain a
routing threshold, and this module should never itself extract text,
run OCR, or call Gemini Vision - it returns a decision, the Orchestrator
acts on it (via the Validator).

Design notes (consistent with the rest of the pipeline):
- Pure functions only: `decide_page_routing` and `_decide_single_page`
  take already-built PageAnalysis/LayoutAnalysis objects and a config,
  and return a plain dataclass. No PDF access, no model calls, no I/O -
  same testability shape as `_is_native_text_trusted` in
  orchestrator.py (which this module's native-text logic deliberately
  mirrors - see `_is_native_text_trusted` below).
- CONSOLIDATION NOTE: `orchestrator.py` currently has its own private
  `_is_native_text_trusted(page_analysis)` implementing the same
  native-text-trust logic reproduced here. That duplication is a
  natural future cleanup (orchestrator could call into this module
  instead) but is NOT resolved by this file - per the current task,
  only this new file is being added, no existing file is being
  touched. Both copies encode the same rule and will not silently
  drift as long as that's kept in mind.
- Every boolean on `PageRoutingDecision` is independent, not a single
  enum/exclusive stage: a page can have use_native=True, use_ocr=True,
  AND use_gemini=True simultaneously (the infographic case - OCR
  supplies grounding text fed into the Gemini Vision prompt, per the
  agreed OCR+Gemini hybrid approach; Gemini isn't asked to do OCR and
  structure recovery unaided). The Validator (not this module)
  interprets these flags to decide which extraction calls to actually
  make.
- `reason` on `PageRoutingDecision` is populated for every decision,
  including "boring" ones (a normal page still records why it got
  simple native-only routing) - consistent with this project's
  "never silently drop/decide without an audit trail" convention
  already used throughout (see SkippedChunk.reason, ValidatedRegionResult.notes,
  IncompletePageCoverageError's explanation).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from multimodal_rag.ingestion.analysis.layout_analysis import LayoutAnalysis
from multimodal_rag.ingestion.analysis.page_preanalyzer import PageAnalysis

logger = logging.getLogger(__name__)


@dataclass
class RoutingPolicyConfig:
    infographic_score_threshold: float = 0.6
    # The single tunable knob controlling the cost/latency-vs-structural-
    # recovery tradeoff: LayoutAnalysis.infographic_score at or above
    # this value routes the page to Gemini Vision. Deliberately just one
    # threshold on the already-computed composite score (rather than
    # separate thresholds on fragmentation_score/alignment_score here)
    # - if the composite needs to weight those two differently, that's
    # LayoutAnalysisConfig's job (layout_analysis.py), not this one's.

    ocr_grounds_gemini: bool = True
    # When True (default), any page routed to Gemini Vision also gets
    # use_ocr=True, so the Validator can pass OCR's text into the
    # Gemini Vision prompt as grounding context (per the agreed
    # OCR+Gemini hybrid: Vision describes structure, OCR supplies exact
    # wording) instead of asking Gemini to read AND describe unaided.
    # Set False to route infographic pages to Gemini alone.


@dataclass
class PageRoutingDecision:
    """
    A DECISION, not a measurement - the one place in this pipeline
    where that's true. Contains no scores or raw signals of its own;
    everything here is a boolean derived from PageAnalysis/LayoutAnalysis
    plus `reason`, an audit trail of which signals produced it.
    """
    page_number: int
    use_native: bool
    use_ocr: bool
    use_gemini: bool
    reason: list[str] = field(default_factory=list)


def _is_native_text_trusted(page_analysis: PageAnalysis | None) -> bool:
    """
    Mirrors orchestrator.py's private `_is_native_text_trusted` - see
    this module's docstring CONSOLIDATION NOTE. Broken-font pages and
    scan candidates both mean "don't trust the text layer," handled
    identically regardless of which signal fired.
    """
    if page_analysis is None:
        return True  # no signal available - default to trusting native text
    return not (page_analysis.has_broken_font_suspect or page_analysis.is_scanned_candidate)


def _is_infographic(layout_analysis: LayoutAnalysis | None, config: RoutingPolicyConfig) -> bool:
    if layout_analysis is None:
        return False  # no layout signal (e.g. blank page) - nothing to route to Vision
    return layout_analysis.infographic_score >= config.infographic_score_threshold


def _decide_single_page(
    page_number: int,
    page_analysis: PageAnalysis | None,
    layout_analysis: LayoutAnalysis | None,
    config: RoutingPolicyConfig,
) -> PageRoutingDecision:
    reason: list[str] = []

    native_trusted = _is_native_text_trusted(page_analysis)
    if page_analysis is None:
        reason.append("no PageAnalysis available - defaulting native text to trusted")
    elif not native_trusted:
        trigger = "broken_font_suspect" if page_analysis.has_broken_font_suspect else "scanned_candidate"
        reason.append(f"native text not trusted ({trigger})")
    else:
        reason.append("native text trusted")

    infographic = _is_infographic(layout_analysis, config)
    if layout_analysis is None:
        reason.append("no LayoutAnalysis available - not routed to Gemini Vision")
    elif infographic:
        reason.append(
            f"infographic_score {layout_analysis.infographic_score:.2f} >= "
            f"threshold {config.infographic_score_threshold:.2f} - routing to Gemini Vision"
        )
    else:
        reason.append(
            f"infographic_score {layout_analysis.infographic_score:.2f} < "
            f"threshold {config.infographic_score_threshold:.2f} - Gemini Vision not needed"
        )

    use_gemini = infographic
    use_ocr = (not native_trusted) or (use_gemini and config.ocr_grounds_gemini)
    use_native = native_trusted

    if use_gemini and config.ocr_grounds_gemini and native_trusted:
        reason.append("OCR also enabled to ground the Gemini Vision prompt (ocr_grounds_gemini)")

    return PageRoutingDecision(
        page_number=page_number,
        use_native=use_native,
        use_ocr=use_ocr,
        use_gemini=use_gemini,
        reason=reason,
    )


def decide_page_routing(
    page_analyses: dict[int, PageAnalysis],
    layout_analyses: dict[int, LayoutAnalysis],
    config: RoutingPolicyConfig | None = None,
) -> dict[int, PageRoutingDecision]:
    """
    Combine per-page PageAnalysis and LayoutAnalysis into a routing
    decision for every page either dict has an entry for. A page
    missing from one dict (e.g. a blank page absent from LayoutAnalysis,
    or a page pre-analysis genuinely has no signal for) is handled via
    `_decide_single_page`'s None-handling, not by silently skipping it -
    every page that reaches this function gets a decision with an
    explicit reason, same "never silently drop" convention used
    everywhere else in this pipeline.

    Pure function: no OCR is run, no Gemini call is made, no I/O of any
    kind - this only ever returns a decision for the Orchestrator (via
    the Validator) to act on.
    """
    config = config or RoutingPolicyConfig()
    page_numbers = set(page_analyses.keys()) | set(layout_analyses.keys())

    decisions: dict[int, PageRoutingDecision] = {}
    for page_number in sorted(page_numbers):
        decisions[page_number] = _decide_single_page(
            page_number,
            page_analyses.get(page_number),
            layout_analyses.get(page_number),
            config,
        )

    gemini_count = sum(1 for d in decisions.values() if d.use_gemini)
    logger.info(
        "Routing policy decided %d page(s): %d routed to Gemini Vision",
        len(decisions), gemini_count,
    )
    return decisions
