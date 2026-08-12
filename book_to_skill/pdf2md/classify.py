"""Page classification, watermark / broken-encoding detection."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Iterable, List, Sequence, Set, Tuple

from .ir import PageType
from .profiles import ConvertProfile

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MOJIBAKE_HINT = re.compile(r"[\u00c0-\u00ff]{3,}|\x00")


def line_set(text: str) -> Set[str]:
    return {ln.strip() for ln in text.splitlines() if ln.strip()}


def garbage_ratio(text: str) -> float:
    """Fraction of characters that look like encoding garbage / NULs / private-use."""
    if not text:
        return 1.0
    bad = 0
    for ch in text:
        if ch == "\x00" or ord(ch) < 9:
            bad += 1
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C") and ch not in "\t\n\r":
            bad += 1
            continue
        if cat == "Co":  # private use
            bad += 1
    return bad / max(len(text), 1)


def repeated_line_candidates(
    page_texts: Sequence[str], fraction: float = 0.5
) -> List[Tuple[str, int]]:
    n = len(page_texts)
    if n == 0:
        return []
    threshold = n * fraction
    counts: Counter[str] = Counter()
    for text in page_texts:
        for ln in line_set(text):
            counts[ln] += 1
    return [(t, c) for t, c in counts.most_common() if c > threshold]


def strip_watermarks(text: str, watermarks: Iterable[str]) -> str:
    wm = set(watermarks)
    if not wm:
        return text
    kept = []
    for ln in text.splitlines():
        if ln.strip() in wm:
            continue
        kept.append(ln)
    return "\n".join(kept)


def classify_page(
    text: str,
    *,
    embedded_image_count: int = 0,
    profile: ConvertProfile,
) -> Tuple[PageType, bool]:
    """Return (page_type, force_ocr)."""
    chars = len(text.strip())
    g = garbage_ratio(text)
    force = False

    if chars < profile.force_ocr_min_chars:
        force = True
        if embedded_image_count > 0 or chars == 0:
            return PageType.IMAGE_BASED, True
        return PageType.SCANNED, True

    if g >= profile.force_ocr_garbage_ratio:
        force = True
        return PageType.BROKEN_ENCODING, True

    if embedded_image_count >= 3 and chars < profile.force_ocr_min_chars * 5:
        return PageType.MIXED, False

    return PageType.NATIVE_TEXT, force


def repeated_line_ratio(text: str) -> float:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    c = Counter(lines)
    top = c.most_common(1)[0][1]
    return top / len(lines)


def looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 120:
        return False
    if s.isupper() and len(s) > 3:
        return True
    if re.match(r"^(\d+(\.\d+){0,4}|[A-Z]\.|附录|Annex|Chapter|CHAPTER)\s+\S", s):
        return True
    return False
