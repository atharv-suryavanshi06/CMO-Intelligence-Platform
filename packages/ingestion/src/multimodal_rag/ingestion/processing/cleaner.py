"""
Cleaner Module
================

Text normalization applied to ALL extracted text regardless of source
(Docling native text, OCR output, table-linearized text) - per the
locked architecture, cleaning is a single shared stage because font/
encoding/whitespace problems show up across every extraction path, not
just one.

Handles, per the locked requirements (S6):
- broken Unicode / mojibake (via ftfy)
- (cid:xx) literal markers that survived extraction (stripped, not
  "repaired" - there is no ToUnicode map to recover the real character
  from, so keeping the token would just inject noise into embeddings)
- ligatures (via Unicode NFKC normalization)
- hyphenated words split across line breaks
- excessive whitespace
- duplicate lines
- repeated headers/footers (recurring identical text across many pages
  of the SAME document - requires document-level context, handled
  separately from single-text cleaning)
- standalone page-number lines

Two-tier design:
- `clean_text()` operates on a single string in isolation (works for a
  single page, a single region, or a single OCR result).
- `detect_repeated_header_footer_lines()` / `clean_document()` operate
  across all pages of a document, because "is this line a repeated
  header" is inherently a cross-page question - a single page has no way
  to know its top line recurs on 40 other pages.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

import ftfy

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class CleaningConfig:
    fix_mojibake: bool = True
    strip_cid_markers: bool = True
    normalize_unicode: bool = True
    dehyphenate: bool = True
    remove_duplicate_lines: bool = True
    strip_page_numbers: bool = True
    collapse_whitespace: bool = True

    # Document-level repeated-header/footer detection
    repeated_line_min_page_fraction: float = 0.6
    # A line must recur on at least this fraction of a document's pages
    # to be treated as a repeated header/footer, not coincidentally
    # identical content (e.g. a short "Overview" heading used on 2 of 40
    # pages should NOT be stripped as a header).
    header_footer_zone_lines: int = 3
    # Only lines within the first/last N lines of a page are eligible to
    # be considered header/footer candidates - a recurring sentence in
    # the middle of the body text is a coincidence, not a running header.


@dataclass
class CleanedText:
    text: str
    applied_fixes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Single-text cleaning
# --------------------------------------------------------------------------

_CID_MARKER_RE = re.compile(r"\(cid:\d+\)|/(?:gid|glyph|g)\d{2,}", re.IGNORECASE)
# Extended to also catch /gidNNNNN-style glyph name tokens, not just
# (cid:NNN) - a second broken-font representation found missing from
# this pipeline's detection entirely until a real document surfaced it.
# This is defense-in-depth cleanup for anything that reaches this stage
# still carrying such tokens; the primary defense is Module 7's
# validator rejecting text containing them BEFORE it's accepted as
# native text (see validator.py's _BROKEN_GLYPH_TOKEN_RE).
_PAGE_NUMBER_LINE_RE = re.compile(
    r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.IGNORECASE
)
# A small, deliberately non-exhaustive set of common hyphenated compounds
# that should NOT be merged into one word during dehyphenation. This is a
# pragmatic heuristic, not a dictionary lookup: distinguishing a genuine
# line-wrap hyphen ("infor-\nmation" -> "information") from a real
# compound word split across a line break ("well-\nknown" -> "well-known")
# is not reliably solvable without a full dictionary, which was judged
# not worth the added dependency for this stage. Extend this set if real
# documents surface more false merges during calibration.
_KNOWN_HYPHENATED_COMPOUNDS = frozenset({
    "well-known", "long-term", "short-term", "self-esteem", "follow-up",
    "state-of-the-art", "up-to-date", "cost-effective", "high-level",
    "low-level", "real-time", "end-to-end", "one-time", "in-depth",
    "co-founder", "pre-existing", "re-evaluate", "non-negotiable",
    "cross-functional", "decision-making", "risk-averse", "data-driven",
    "user-friendly", "open-source", "third-party", "full-time", "part-time",
})
_DEHYPHENATE_RE = re.compile(r"([A-Za-z]+)-\n([A-Za-z]+)")


def _fix_mojibake(text: str) -> tuple[str, bool]:
    fixed = ftfy.fix_text(text)
    return fixed, fixed != text


def _strip_cid_markers(text: str) -> tuple[str, int]:
    fixed, count = _CID_MARKER_RE.subn("", text)
    return fixed, count


def _normalize_unicode(text: str) -> tuple[str, bool]:
    # NFKC normalization decomposes/recomposes compatibility characters,
    # including ligatures (ﬁ -> fi, ﬂ -> fl) and other typographic
    # variants (full-width forms, etc) into their standard equivalents.
    normalized = unicodedata.normalize("NFKC", text)
    return normalized, normalized != text


def _dehyphenate(text: str) -> tuple[str, int]:
    count = 0

    def _replace(match: re.Match) -> str:
        nonlocal count
        w1, w2 = match.group(1), match.group(2)
        combined_hyphenated = f"{w1}-{w2}".lower()
        if combined_hyphenated in _KNOWN_HYPHENATED_COMPOUNDS:
            count += 1
            return f"{w1}-{w2}"  # keep the hyphen, just rejoin the line
        if w2[0].isupper():
            # Likely a new sentence/proper noun starting right after the
            # break, not a continued word - leave the hyphen and break as
            # a space instead of guessing a merge.
            return f"{w1}-{w2}"
        count += 1
        return f"{w1}{w2}"

    fixed = _DEHYPHENATE_RE.sub(_replace, text)
    return fixed, count


def _remove_duplicate_lines(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    result: list[str] = []
    removed = 0
    prev_stripped = None
    for line in lines:
        stripped = line.strip()
        if stripped and stripped == prev_stripped:
            removed += 1
            continue
        result.append(line)
        prev_stripped = stripped if stripped else prev_stripped
    return "\n".join(result), removed


def _strip_page_number_lines(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    kept = []
    removed = 0
    for line in lines:
        if _PAGE_NUMBER_LINE_RE.match(line):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def _collapse_whitespace(text: str) -> str:
    # Collapse runs of horizontal whitespace, strip trailing whitespace
    # per line, and cap blank-line runs at one (paragraph break) instead
    # of allowing arbitrarily long vertical gaps.
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in text.split("\n")]
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                collapsed.append(line)
        else:
            blank_run = 0
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def clean_text(text: str, config: CleaningConfig | None = None) -> CleanedText:
    """
    Clean a single string. Order matters: mojibake/cid/unicode fixes run
    before structural fixes (dehyphenation, duplicate/page-number line
    removal), which run before final whitespace collapsing - each step
    assumes the previous one has already normalized the character-level
    content it operates on.
    """
    config = config or CleaningConfig()
    applied: list[str] = []

    if config.fix_mojibake:
        text, changed = _fix_mojibake(text)
        if changed:
            applied.append("fixed_mojibake")

    if config.strip_cid_markers:
        text, count = _strip_cid_markers(text)
        if count:
            applied.append(f"stripped_{count}_cid_markers")

    if config.normalize_unicode:
        text, changed = _normalize_unicode(text)
        if changed:
            applied.append("normalized_unicode_ligatures")

    if config.dehyphenate:
        text, count = _dehyphenate(text)
        if count:
            applied.append(f"dehyphenated_{count}_words")

    if config.remove_duplicate_lines:
        text, count = _remove_duplicate_lines(text)
        if count:
            applied.append(f"removed_{count}_duplicate_lines")

    if config.strip_page_numbers:
        text, count = _strip_page_number_lines(text)
        if count:
            applied.append(f"removed_{count}_page_number_lines")

    if config.collapse_whitespace:
        text = _collapse_whitespace(text)
        applied.append("collapsed_whitespace")

    return CleanedText(text=text, applied_fixes=applied)


# --------------------------------------------------------------------------
# Document-level: repeated header/footer detection
# --------------------------------------------------------------------------

def detect_repeated_header_footer_lines(
    page_texts: list[str], config: CleaningConfig | None = None
) -> set[str]:
    """
    Find lines that recur across a large fraction of a document's pages
    within the header/footer zone (first/last N lines) - these are
    treated as running headers/footers to strip, not real content.

    Deliberately requires the WHOLE document's pages up front (not a
    streaming/incremental design) because "repeated" is only meaningful
    once you've seen enough of the document to know what recurs.
    """
    config = config or CleaningConfig()
    if len(page_texts) < 2:
        return set()  # "repeated" is meaningless for a single-page document

    zone_line_counts: Counter[str] = Counter()
    for page_text in page_texts:
        lines = [l.strip() for l in page_text.split("\n") if l.strip()]
        zone = lines[: config.header_footer_zone_lines] + lines[-config.header_footer_zone_lines:]
        for line in set(zone):  # count each page at most once per distinct line
            zone_line_counts[line] += 1

    min_pages = max(2, int(len(page_texts) * config.repeated_line_min_page_fraction))
    return {line for line, count in zone_line_counts.items() if count >= min_pages}


def strip_known_lines(text: str, lines_to_strip: set[str]) -> tuple[str, int]:
    if not lines_to_strip:
        return text, 0
    kept = []
    removed = 0
    for line in text.split("\n"):
        if line.strip() in lines_to_strip:
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def clean_document(
    page_texts: list[str], config: CleaningConfig | None = None
) -> list[CleanedText]:
    """
    Clean every page of a document, including cross-page repeated
    header/footer stripping. Per-page single-text cleaning runs first so
    that header/footer detection compares already-normalized lines
    (e.g. mojibake-fixed) rather than raw, potentially-differently-broken
    versions of what is conceptually the same header line.
    """
    config = config or CleaningConfig()
    cleaned = [clean_text(t, config) for t in page_texts]

    repeated_lines = detect_repeated_header_footer_lines(
        [c.text for c in cleaned], config
    )
    if repeated_lines:
        logger.info(
            "Detected %d repeated header/footer line(s) across %d pages: %s",
            len(repeated_lines), len(page_texts),
            [l[:60] for l in list(repeated_lines)[:5]],
        )
        for c in cleaned:
            new_text, removed = strip_known_lines(c.text, repeated_lines)
            if removed:
                # Re-collapse: removing a header/footer line can leave a
                # stray leading/trailing blank line that the earlier
                # whitespace pass (which ran before this line existed)
                # had no chance to clean up.
                c.text = _collapse_whitespace(new_text)
                c.applied_fixes.append(f"stripped_{removed}_repeated_header_footer_lines")

    return cleaned
