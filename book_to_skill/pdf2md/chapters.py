"""Chapter boundary detection for a PDF (TOC → heading regex → none).

Detection failure is a legal outcome. Fabricating a single catch-all chapter is not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .classify import repeated_line_candidates, strip_watermarks
from .optimize.net_guard import install_guard

# Same title at similar y on this many pages → running header, not a chapter start.
_HEADER_MIN_PAGES = 3
_HEADER_Y_TOL_PT = 12.0

# Distinct chapter-title hits on one page → that page is a contents listing.
_TOC_PAGE_MIN_TITLES = 3

# Numbered Latin headings (IEC-style "10 Capacitors…") only count near the top.
_NUM_HEADING_TOP_FRAC = 0.22

_MIN_USABLE_CHARS = 40

_CN_HEADING = re.compile(
    r"^第\s*([0-9０-９一二三四五六七八九十百千两零〇]+)\s*章\s*(.*)$"
)
_CHAPTER_HEADING = re.compile(
    r"^(?:Chapter|CHAPTER|Ch\.?)\s+(\d{1,2})\b\s*(.*)$"
)
_NUM_HEADING = re.compile(r"^(\d{1,2})\s+([A-Z][A-Za-z].*)$")
_NUM_ONLY = re.compile(r"^(\d{1,2})$")
_LEADERS = re.compile(r"[.．。·…]{4,}|\.{8,}|…{2,}")
_TOC_HEADER_LINE = re.compile(
    r"^(?:目\s*录|目\s*次|目錄|CONTENTS|TABLE\s+OF\s+CONTENTS)$",
    re.IGNORECASE,
)
_FRONT_MATTER_TITLE = re.compile(
    r"^(前言|序言|序|FOREWORD|PREFACE|INTRODUCTION)\s*$",
    re.IGNORECASE,
)
_CN_NUM_VALUES = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_NUM_UNITS = {"十": 10, "百": 100, "千": 1000}


@dataclass(frozen=True)
class _Line:
    page: int
    text: str
    y: float
    size: float
    page_height: float


@dataclass
class _Hit:
    page: int
    number: int
    title: str
    y: float
    size: float
    page_height: float


def detect_chapters(pdf_path: Path) -> dict:
    """Detect chapter boundaries. Never invent a chapter when none are found."""
    install_guard(allow_loopback=True)
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))

    import fitz

    doc = fitz.open(path)
    try:
        page_count = len(doc)
        toc_raw = list(doc.get_toc() or [])
        pages_text, lines = _extract_pages(doc)
    finally:
        doc.close()

    warnings: list[str] = []
    usable, usable_reason = _text_layer_usable(pages_text)

    if toc_raw:
        accepted, toc_warning = _validate_toc(toc_raw, page_count)
        if toc_warning:
            warnings.append(toc_warning)
        if accepted:
            chapters = _chapters_from_toc(toc_raw, page_count, pages_text)
            return _result("toc", page_count, chapters, warnings)

    if not usable:
        warnings.append(usable_reason)
        return _result("none", page_count, [], warnings)

    hits = _heading_hits(lines)
    hits = _drop_running_headers(hits)
    hits = _drop_toc_page_hits(hits, pages_text)
    hits = _first_hit_per_page(hits)
    hits = _first_hit_per_number(hits)
    chapters = _hits_to_chapters(hits, page_count, pages_text)
    if not chapters:
        warnings.append("no chapter headings detected after header/TOC-page filters")
        return _result("none", page_count, [], warnings)
    return _result("heading", page_count, chapters, warnings)


def write_chapters_json(data: dict, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def format_chapters_text(data: dict) -> str:
    lines = [
        f"source: {data.get('source')}",
        f"pages: {data.get('page_count')}",
        f"chapters: {len(data.get('chapters') or [])}",
    ]
    for ch in data.get("chapters") or []:
        lines.append(
            f"  {ch['index']:>2}  {ch['title']}  {ch['start_page']}-{ch['end_page']}"
        )
    warnings = data.get("warnings") or []
    if warnings:
        lines.append("warnings:")
        for w in warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines) + "\n"


def _result(source: str, page_count: int, chapters: list[dict], warnings: list[str]) -> dict:
    return {
        "source": source,
        "page_count": page_count,
        "chapters": chapters,
        "warnings": list(warnings),
    }


def _extract_pages(doc: Any) -> tuple[list[str], list[_Line]]:
    pages_text: list[str] = []
    lines: list[_Line] = []
    for i, page in enumerate(doc):
        raw = page.get_text("text") or ""
        pages_text.append(raw)
        height = float(page.rect.height) or 1.0
        extracted = _lines_from_dict(page.get_text("dict"), i + 1, height)
        lines.extend(_merge_split_headings(extracted))
    return pages_text, lines


def _lines_from_dict(data: dict, page: int, page_height: float) -> list[_Line]:
    out: list[_Line] = []
    for block in data.get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            spans = line.get("spans") or []
            text = "".join(str(s.get("text") or "") for s in spans).strip()
            if not text:
                continue
            sizes = [float(s.get("size") or 0.0) for s in spans]
            size = max(sizes) if sizes else 0.0
            bbox = line.get("bbox") or (0, 0, 0, 0)
            out.append(
                _Line(
                    page=page,
                    text=text,
                    y=float(bbox[1]),
                    size=size,
                    page_height=page_height,
                )
            )
    return out


def _merge_split_headings(lines: list[_Line]) -> list[_Line]:
    """Join '1' + 'Scope' and '第1 章' + title when they sit on consecutive lines."""
    if not lines:
        return []
    merged: list[_Line] = []
    skip_next = False
    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt is None or nxt.page != line.page:
            merged.append(line)
            continue
        if _LEADERS.search(nxt.text):
            merged.append(line)
            continue
        dy = abs(nxt.y - line.y)
        cn = _CN_HEADING.match(line.text)
        if (
            cn
            and not (cn.group(2) or "").strip()
            and dy <= 80
            and _looks_like_title_tail(nxt.text)
        ):
            merged.append(
                _Line(
                    page=line.page,
                    text=f"{line.text} {nxt.text}".strip(),
                    y=line.y,
                    size=max(line.size, nxt.size),
                    page_height=line.page_height,
                )
            )
            skip_next = True
            continue
        ch = _CHAPTER_HEADING.match(line.text)
        if (
            ch
            and not (ch.group(2) or "").strip()
            and dy <= 80
            and _looks_like_title_tail(nxt.text)
        ):
            merged.append(
                _Line(
                    page=line.page,
                    text=f"{line.text} {nxt.text}".strip(),
                    y=line.y,
                    size=max(line.size, nxt.size),
                    page_height=line.page_height,
                )
            )
            skip_next = True
            continue
        if _NUM_ONLY.match(line.text) and _NUM_HEADING.match(f"{line.text} {nxt.text}"):
            # IEC splits "1" and "Scope" on the same baseline; table cells are not.
            same_line = abs(nxt.y - line.y) <= 3.0
            heading_size = line.size >= 10.0
            if same_line and heading_size and (
                _looks_like_title_tail(nxt.text) or (
                    len(nxt.text) >= 4 and not nxt.text.endswith((".", "。", "!", "？", "?"))
                )
            ):
                merged.append(
                    _Line(
                        page=line.page,
                        text=f"{line.text} {nxt.text}".strip(),
                        y=line.y,
                        size=max(line.size, nxt.size),
                        page_height=line.page_height,
                    )
                )
                skip_next = True
                continue
        merged.append(line)
    return merged


def _looks_like_title_tail(text: str) -> bool:
    s = text.strip()
    if not s or len(s) > 40 or _LEADERS.search(s):
        return False
    if s.endswith((".", "。", "!", "？", "?", ";", "；")):
        return False
    if _CN_HEADING.match(s) or _CHAPTER_HEADING.match(s) or _NUM_HEADING.match(s):
        return False
    if s.isdigit():
        return False
    return True


def _text_layer_usable(pages_text: list[str]) -> tuple[bool, str]:
    n = len(pages_text)
    if n == 0:
        return False, "no pages"
    raw_lens = [len(t) for t in pages_text]
    if sum(raw_lens) == 0:
        return False, "no text layer"
    watermarks = [t for t, _ in repeated_line_candidates(pages_text, fraction=0.5)]
    remaining = [strip_watermarks(t, watermarks) for t in pages_text]
    rem_lens = [len(r.strip()) for r in remaining]
    median_rem = sorted(rem_lens)[n // 2]
    mean_raw = sum(raw_lens) / n
    unique = {t.strip() for t in pages_text}
    if n >= 3 and len(unique) == 1 and mean_raw < 200:
        return False, (
            f"text layer unusable: identical repeating watermark on all {n} pages "
            f"({mean_raw:.0f} chars/page)"
        )
    if median_rem < _MIN_USABLE_CHARS and median_rem < 0.3 * mean_raw:
        return False, (
            f"text layer unusable: median {median_rem} chars/page after watermark strip "
            f"(raw mean {mean_raw:.0f})"
        )
    return True, ""


def _validate_toc(toc: list, page_count: int) -> tuple[bool, str | None]:
    """Return (accepted, warning). Isight-style outlines must be rejected."""
    entries = _normalized_toc(toc, page_count)
    n = len(entries)
    if n < 2:
        return False, f"embedded TOC rejected: {n} usable entries"

    levels = [e[0] for e in entries]
    unique_levels = set(levels)
    shallow = min(unique_levels)
    n_shallow = sum(1 for lv in levels if lv == shallow)
    cap = page_count / 10.0
    multi_level = len(unique_levels) >= 2
    sparse = n <= cap

    if not (multi_level or sparse):
        return False, (
            f"embedded TOC rejected: {n_shallow}/{n} entries at level {shallow}"
        )
    if n_shallow > cap:
        return False, (
            f"embedded TOC rejected: {n_shallow}/{n} entries at level {shallow}"
        )

    first_page = entries[0][2]
    if first_page > page_count * 0.1:
        return False, (
            f"embedded TOC rejected: first entry starts at page {first_page} "
            f"(> {page_count} * 0.1)"
        )
    return True, None


def _normalized_toc(toc: list, page_count: int) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    for item in toc:
        if len(item) < 3:
            continue
        level = int(item[0])
        title = str(item[1]).strip()
        page = int(item[2])
        if not title or page < 1 or page > page_count:
            continue
        out.append((level, title, page))
    return out


def _chapters_from_toc(
    toc: list, page_count: int, pages_text: list[str]
) -> list[dict]:
    entries = _normalized_toc(toc, page_count)
    shallow = min(e[0] for e in entries)
    chosen = [e for e in entries if e[0] == shallow]
    # One boundary per page: keep the first title at that page.
    by_page: dict[int, tuple[int, str, int]] = {}
    for e in chosen:
        by_page.setdefault(e[2], e)
    ordered = sorted(by_page.values(), key=lambda e: e[2])
    starts = [(title, page) for _, title, page in ordered]
    return _build_chapter_dicts(starts, page_count, pages_text, level=1)


def _heading_hits(lines: Iterable[_Line]) -> list[_Hit]:
    hits: list[_Hit] = []
    for line in lines:
        parsed = _parse_heading_line(line)
        if parsed is None:
            continue
        hits.append(parsed)
    return hits


def _parse_heading_line(line: _Line) -> _Hit | None:
    text = line.text.strip()
    if not text or len(text) > 120:
        return None
    if _LEADERS.search(text):
        return None

    m = _CN_HEADING.match(text)
    if m:
        number = _cn_numeral_to_int(m.group(1))
        if number is None:
            return None
        title = _clean_title(text)
        if not _title_ok(title):
            return None
        return _Hit(line.page, number, title, line.y, line.size, line.page_height)

    m = _CHAPTER_HEADING.match(text)
    if m:
        rest = (m.group(2) or "").strip()
        if rest and not _heading_tail_ok(rest):
            return None
        if rest.endswith((".", "。", "!", "？", "?")):
            return None
        title = _clean_title(text)
        if not _title_ok(title):
            return None
        return _Hit(line.page, int(m.group(1)), title, line.y, line.size, line.page_height)

    m = _NUM_HEADING.match(text)
    if m:
        if line.size < 10.0:
            return None
        if line.y > line.page_height * _NUM_HEADING_TOP_FRAC:
            return None
        rest = m.group(2).strip()
        if len(rest) < 4:
            return None
        title = _clean_title(text)
        return _Hit(line.page, int(m.group(1)), title, line.y, line.size, line.page_height)
    return None


def _heading_tail_ok(rest: str) -> bool:
    if rest == "":
        return True
    return bool(re.match(r"^[.:\-—–]|[A-ZÀ-Þ0-9\"“(]|[\u4e00-\u9fff]", rest))


def _clean_title(text: str) -> str:
    s = re.sub(r"\s+", " ", text).strip()
    s = _LEADERS.sub("", s).strip(" .-–—")
    return s


def _title_ok(title: str) -> bool:
    if not title or len(title) > 80:
        return False
    if any(ch in title for ch in r'\/'):
        return False
    if title.endswith((".", "。", "!", "？", "?", ";", "；")):
        return False
    return True


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", "", title).casefold()


def _drop_running_headers(hits: list[_Hit]) -> list[_Hit]:
    """Same title text at similar y on ≥3 pages is a header, not a chapter start."""
    by_title: dict[str, list[int]] = {}
    for i, hit in enumerate(hits):
        by_title.setdefault(_norm_title(hit.title), []).append(i)

    drop: set[int] = set()
    for idxs in by_title.values():
        remaining = list(idxs)
        while remaining:
            seed = remaining[0]
            cluster = [
                j for j in remaining if abs(hits[j].y - hits[seed].y) <= _HEADER_Y_TOL_PT
            ]
            pages = {hits[j].page for j in cluster}
            if len(pages) >= _HEADER_MIN_PAGES:
                drop.update(cluster)
            remaining = [j for j in remaining if j not in cluster]
    return [h for i, h in enumerate(hits) if i not in drop]


def _drop_toc_page_hits(hits: list[_Hit], pages_text: list[str]) -> list[_Hit]:
    titles_on_page: dict[int, set[str]] = {}
    for hit in hits:
        titles_on_page.setdefault(hit.page, set()).add(_norm_title(hit.title))

    toc_pages: set[int] = set()
    for page_no, text in enumerate(pages_text, start=1):
        n_titles = len(titles_on_page.get(page_no, set()))
        if _is_toc_page(text, n_titles):
            toc_pages.add(page_no)
    return [h for h in hits if h.page not in toc_pages]


def _is_toc_page(text: str, n_distinct_titles: int) -> bool:
    if n_distinct_titles >= _TOC_PAGE_MIN_TITLES:
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    n_leaders = 0
    if lines:
        n_leaders = sum(
            1 for ln in lines if _LEADERS.search(ln) or ln.count(".") >= 8
        )
        if n_leaders >= 8:
            return True
        if n_leaders >= 5 and n_leaders / len(lines) >= 0.25:
            return True
    if _page_has_toc_header(text) and (n_distinct_titles >= 1 or n_leaders >= 3):
        return True
    return False


def _page_has_toc_header(text: str) -> bool:
    """True only for a contents heading, not a body phrase like '运行目录'."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:12]
    for ln in lines:
        if _TOC_HEADER_LINE.match(ln):
            return True
    for i in range(len(lines) - 1):
        joined = re.sub(r"\s+", "", lines[i] + lines[i + 1])
        if joined in {"目录", "目次", "目錄"}:
            return True
    return False


def _first_hit_per_page(hits: list[_Hit]) -> list[_Hit]:
    by_page: dict[int, _Hit] = {}
    for hit in sorted(hits, key=lambda h: (h.page, h.y)):
        by_page.setdefault(hit.page, hit)
    return [by_page[p] for p in sorted(by_page)]


def _first_hit_per_number(hits: list[_Hit]) -> list[_Hit]:
    by_num: dict[int, _Hit] = {}
    for hit in sorted(hits, key=lambda h: (h.page, h.y)):
        by_num.setdefault(hit.number, hit)
    return sorted(by_num.values(), key=lambda h: (h.page, h.y))


def _hits_to_chapters(
    hits: list[_Hit], page_count: int, pages_text: list[str]
) -> list[dict]:
    if not hits:
        return []
    starts = [(h.title, h.page) for h in hits]
    return _build_chapter_dicts(starts, page_count, pages_text, level=1)


def _build_chapter_dicts(
    starts: list[tuple[str, int]],
    page_count: int,
    pages_text: list[str],
    *,
    level: int,
) -> list[dict]:
    if not starts:
        return []
    chapters: list[dict] = []
    first_start = starts[0][1]
    if first_start > 1:
        chapters.append(
            {
                "index": 0,
                "title": _front_matter_title(pages_text, first_start),
                "level": 0,
                "start_page": 1,
                "end_page": first_start - 1,
            }
        )
    for i, (title, start) in enumerate(starts):
        if i + 1 < len(starts):
            end = starts[i + 1][1] - 1
        else:
            end = page_count
        if end < start:
            end = start
        chapters.append(
            {
                "index": i + 1,
                "title": title,
                "level": level,
                "start_page": start,
                "end_page": end,
            }
        )
    return chapters


def _front_matter_title(pages_text: list[str], first_chapter_page: int) -> str:
    limit = min(len(pages_text), first_chapter_page - 1)
    for i in range(limit):
        for raw in (pages_text[i] or "").splitlines():
            s = raw.strip()
            if _FRONT_MATTER_TITLE.match(s):
                return s
    return "front-matter"


def _cn_numeral_to_int(s: str) -> int | None:
    if s.isdigit():
        n = int(s)
        return n if 1 <= n <= 999 else None
    section = current = 0
    for ch in s:
        if ch in _CN_NUM_VALUES:
            current = _CN_NUM_VALUES[ch]
        elif ch in _CN_NUM_UNITS:
            section += (current or 1) * _CN_NUM_UNITS[ch]
            current = 0
        else:
            return None
    total = section + current
    return total if 1 <= total <= 999 else None
