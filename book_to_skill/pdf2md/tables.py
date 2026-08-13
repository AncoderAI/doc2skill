"""Table extraction and Markdown/HTML round-trip helpers."""

from __future__ import annotations

import re
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .ir import TableBlock, TableCell

BBox = Tuple[float, float, float, float]
WordBox = Tuple[BBox, str]


def is_scorable_table(obj: Union[TableBlock, Dict[str, Any], None]) -> bool:
    """Gate: real tables only — rows≥1, cols≥2, non-empty cells, bbox present.

    Caption shells (rows=0/cols=0/bbox=None) are not tables. They may be stored
    elsewhere as caption mentions; they must not enter the tables ref/hyp pools.
    """
    if obj is None:
        return False
    if isinstance(obj, TableBlock):
        rows = int(obj.rows or 0)
        cols = int(obj.cols or 0)
        cells = obj.cells or []
        bbox = obj.bbox
    elif isinstance(obj, dict):
        rows = int(obj.get("rows") or 0)
        cols = int(obj.get("cols") or 0)
        cells = obj.get("cells") or []
        bbox = obj.get("bbox")
    else:
        return False
    if rows < 1 or cols < 2:
        return False
    if not cells:
        return False
    if not bbox or len(bbox) < 4:
        return False
    return True


def extract_tables_pdfplumber(pdf_path: str, page_index: int) -> List[TableBlock]:
    """Extract tables from a single 0-based page via pdfplumber."""
    from .handles import get_plumber_page

    out: List[TableBlock] = []
    page = get_plumber_page(pdf_path, page_index)
    if page is None:
        return out
    found = page.find_tables() or []
    for t in found:
        data = t.extract() or []
        block = grid_to_table(data, bbox=tuple(t.bbox) if t.bbox else None)
        if block is not None and is_scorable_table(block):
            out.append(block)
    return out


def extract_tables_img2table(
    image,
    *,
    page_w: float,
    page_h: float,
    dpi: int = 300,
    borderless: bool = True,
) -> List[TableBlock]:
    """Extract tables from a rendered page image via img2table + local tesseract.

    Product extraction path — OpenCV geometry, no layout ML. Coordinates are
    converted from image pixels (top-left) to PDF bottom-left points.
    """
    from io import BytesIO

    try:
        from img2table.document import Image as I2TImage
        from img2table.ocr import TesseractOCR
    except ImportError as exc:
        detail = str(exc)
        if "cv2" in detail.lower() or "opencv" in detail.lower():
            missing = (
                "opencv-python-headless (cv2), required by img2table"
            )
        else:
            missing = "img2table"
        raise ImportError(
            f"{missing} is not installed; scanned-page table extraction cannot "
            "use img2table and falls back to OCR word-box projection. "
            "Install: pip install 'book-to-skill[pdf2md-scan-tables]'. "
            f"Original error: {exc}"
        ) from exc

    if image is None:
        return []
    buf = BytesIO()
    if hasattr(image, "save"):
        image.save(buf, format="PNG")
    else:
        from PIL import Image as PILImage
        import numpy as np

        PILImage.fromarray(np.asarray(image)).save(buf, format="PNG")
    ocr = TesseractOCR(n_threads=1, lang="eng+deu")
    doc = I2TImage(buf.getvalue())
    try:
        tables = doc.extract_tables(
            ocr=ocr,
            implicit_rows=borderless,
            implicit_columns=borderless,
            borderless_tables=borderless,
            min_confidence=40,
        )
    except TypeError:
        tables = doc.extract_tables(ocr=ocr, borderless_tables=borderless)
    except Exception:
        return []
    scale = dpi / 72.0
    out: List[TableBlock] = []
    for t in tables or []:
        content = getattr(t, "content", None) or {}
        if not content:
            continue
        max_row = max(content.keys())
        cols = 0
        for r in range(max_row + 1):
            cells_r = content.get(r) or []
            cols = max(cols, len(cells_r))
        grid: List[List[str]] = []
        for r in range(max_row + 1):
            cells_r = content.get(r) or []
            row_vals = []
            for c in range(cols):
                cell = cells_r[c] if c < len(cells_r) else None
                txt = ""
                if cell is not None:
                    raw = getattr(cell, "value", None)
                    txt = "" if raw is None else str(raw).strip()
                row_vals.append(txt)
            grid.append(row_vals)
        bbox_px = getattr(t, "bbox", None)
        bbox = None
        if bbox_px is not None:
            x0 = float(getattr(bbox_px, "x1", 0)) / scale
            x1 = float(getattr(bbox_px, "x2", 0)) / scale
            y_top = float(getattr(bbox_px, "y1", 0)) / scale
            y_bot = float(getattr(bbox_px, "y2", 0)) / scale
            bbox = (x0, page_h - y_bot, x1, page_h - y_top)
            if bbox[3] < bbox[1]:
                bbox = (bbox[0], bbox[3], bbox[2], bbox[1])
        block = grid_to_table(grid, bbox=bbox, header_rows=1)
        if block is not None and is_scorable_table(block):
            out.append(block)
    return out


def _yc(bb: BBox) -> float:
    return (bb[1] + bb[3]) / 2.0


def _xc(bb: BBox) -> float:
    return (bb[0] + bb[2]) / 2.0


def _cluster_words_into_rows(words: Sequence[WordBox]) -> List[List[WordBox]]:
    heights = [max(1.0, bb[3] - bb[1]) for bb, _ in words]
    med_h = statistics.median(heights) if heights else 8.0
    gap = max(med_h * 0.85, 3.5)
    ordered = sorted(words, key=lambda wt: (-_yc(wt[0]), _xc(wt[0])))
    rows: List[List[WordBox]] = []
    cur: List[WordBox] = []
    last_y: Optional[float] = None
    for wt in ordered:
        y = _yc(wt[0])
        if last_y is None or abs(last_y - y) <= gap:
            cur.append(wt)
        else:
            if cur:
                rows.append(sorted(cur, key=lambda w: _xc(w[0])))
            cur = [wt]
        last_y = y
    if cur:
        rows.append(sorted(cur, key=lambda w: _xc(w[0])))
    return rows


def _cluster_1d(values: Sequence[float], gap: float) -> List[float]:
    if not values:
        return []
    ordered = sorted(values)
    clusters: List[List[float]] = [[ordered[0]]]
    for v in ordered[1:]:
        if abs(v - clusters[-1][-1]) <= gap:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def _row_is_tabular(row: Sequence[WordBox]) -> bool:
    if len(row) < 2:
        return False
    digitish = sum(1 for _, t in row if any(c.isdigit() for c in t))
    # multi-column text row or numeric-heavy row
    return len(row) >= 3 or digitish >= 1


def _tabular_row_bands(rows: Sequence[List[WordBox]]) -> List[List[List[WordBox]]]:
    """Contiguous bands preferring digit-bearing rows (failure-rate grids)."""
    digit_flags = [
        any(any(c.isdigit() for c in t) for _, t in r) for r in rows
    ]
    tab_flags = [_row_is_tabular(r) for r in rows]
    bands: List[List[List[WordBox]]] = []
    # Prefer runs of digit rows (bridge at most one non-digit tabular row)
    i = 0
    n = len(rows)
    while i < n:
        if not digit_flags[i]:
            i += 1
            continue
        j = i
        miss = 0
        while j < n:
            if digit_flags[j]:
                miss = 0
                j += 1
            elif tab_flags[j] and miss < 1:
                miss += 1
                j += 1
            else:
                break
        end = j - (miss if miss and not digit_flags[j - 1] else 0)
        band = [rows[k] for k in range(i, max(end, i + 1)) if tab_flags[k] or digit_flags[k]]
        if len(band) >= 2:
            bands.append(band)
        i = max(end, i + 1)
    if bands:
        return bands
    # Fallback: any tabular runs
    i = 0
    while i < n:
        if not tab_flags[i]:
            i += 1
            continue
        j = i
        miss = 0
        while j < n:
            if tab_flags[j]:
                miss = 0
                j += 1
            else:
                miss += 1
                if miss >= 2:
                    break
                j += 1
        end = j - miss
        band = [rows[k] for k in range(i, end) if tab_flags[k]]
        if len(band) >= 2:
            bands.append(band)
        i = max(end, i + 1)
    return bands


def _column_centers_aligned(
    band: Sequence[Sequence[WordBox]],
    *,
    bin_size: float = 14.0,
    min_row_frac: float = 0.2,
) -> List[float]:
    """Column centres = x-bins that recur across many rows (alignment vote)."""
    from collections import Counter

    votes: Counter = Counter()
    for row in band:
        seen = set()
        for bb, _ in row:
            seen.add(int(_xc(bb) / bin_size))
        for b in seen:
            votes[b] += 1
    min_votes = max(2, int(len(band) * min_row_frac))
    peaks = sorted(b for b, v in votes.items() if v >= min_votes)
    if len(peaks) < 2:
        # loosen once
        min_votes = max(2, int(len(band) * 0.12))
        peaks = sorted(b for b, v in votes.items() if v >= min_votes)
    merged: List[List[int]] = []
    for b in peaks:
        if merged and b - merged[-1][-1] <= 1:
            merged[-1].append(b)
        else:
            merged.append([b])
    return [(sum(m) / len(m) + 0.5) * bin_size for m in merged]


def _band_to_table(
    band: Sequence[Sequence[WordBox]],
    *,
    page_w: float,
    page_h: float,
) -> Optional[TableBlock]:
    if len(band) < 2:
        return None
    widths = [max(1.0, bb[2] - bb[0]) for row in band for bb, _ in row]
    med_w = statistics.median(widths) if widths else 12.0
    col_centers = _column_centers_aligned(band)
    if len(col_centers) < 2:
        # Fallback: gap cluster (works when columns are sparse)
        xs = [_xc(bb) for row in band for bb, _ in row]
        x_gap = max(med_w * 2.5, page_w * 0.05)
        col_centers = _cluster_1d(xs, x_gap)
    if len(col_centers) < 2:
        return None
    assign_thresh = max(med_w * 2.2, page_w * 0.035)
    grid: List[List[str]] = []
    all_bbs: List[BBox] = []
    for row in band:
        cell_parts: List[List[Tuple[float, str]]] = [[] for _ in col_centers]
        for bb, text in row:
            x = _xc(bb)
            j = min(range(len(col_centers)), key=lambda k: abs(col_centers[k] - x))
            if abs(col_centers[j] - x) > assign_thresh:
                continue
            cell_parts[j].append((x, text))
            all_bbs.append(bb)
        cells = [""] * len(col_centers)
        for j, parts in enumerate(cell_parts):
            parts.sort(key=lambda p: p[0])
            cells[j] = " ".join(p[1] for p in parts).strip()
        if any(cells):
            grid.append(cells)
    if len(grid) < 2 or not all_bbs:
        return None
    nonempty_cols = [
        c for c in range(len(col_centers)) if sum(1 for r in grid if r[c]) >= 2
    ]
    if len(nonempty_cols) < 2:
        return None
    if len(nonempty_cols) < len(col_centers):
        grid = [[row[c] for c in nonempty_cols] for row in grid]
    bbox = (
        min(b[0] for b in all_bbs),
        min(b[1] for b in all_bbs),
        max(b[2] for b in all_bbs),
        max(b[3] for b in all_bbs),
    )
    area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
    page_area = max(page_w * page_h, 1.0)
    if area / page_area > 0.85:
        return None
    # Prefer grids that contain some digits (SIEMENS failure-rate signal)
    digit_cells = sum(
        1 for row in grid for cell in row if any(c.isdigit() for c in cell)
    )
    if digit_cells < 3:
        return None
    # Reject "tables" that are mostly long prose cells
    long_cells = sum(1 for row in grid for cell in row if len(cell) > 40)
    if long_cells > max(2, len(grid) * len(grid[0]) // 3):
        return None
    return grid_to_table(grid, bbox=bbox, header_rows=1)


def extract_tables_from_ocr_words(
    word_boxes: Sequence[WordBox],
    *,
    page_w: float,
    page_h: float,
) -> List[TableBlock]:
    """Build real tables from Tesseract word boxes via x/y projection clustering.

    Rows: cluster word centres on Y. Columns: alignment votes on X (recurring
    bins across rows), with gap-clustering fallback. Bilingual pages are also
    split into left/right halves so DE/EN columns do not fuse into one band.
    Only emits blocks that pass ``is_scorable_table``.
    """
    words = [(tuple(bb), str(t).strip()) for bb, t in word_boxes if t and str(t).strip()]
    if len(words) < 8:
        return []
    regions: List[List[WordBox]] = [
        words,
        [(bb, t) for bb, t in words if _xc(bb) < page_w * 0.70],
        [(bb, t) for bb, t in words if _xc(bb) >= page_w * 0.40],
    ]
    out: List[TableBlock] = []
    seen_bbox: List[BBox] = []

    def _overlap_frac(a: BBox, b: BBox) -> float:
        ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
        ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
        iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        return inter / aa if aa > 0 else 0.0

    for region in regions:
        if len(region) < 8:
            continue
        rows = _cluster_words_into_rows(region)
        if len(rows) < 2:
            continue
        for band in _tabular_row_bands(rows):
            block = _band_to_table(band, page_w=page_w, page_h=page_h)
            if block is None or not is_scorable_table(block):
                continue
            assert block.bbox is not None
            if any(_overlap_frac(block.bbox, prev) > 0.6 for prev in seen_bbox):
                continue
            seen_bbox.append(block.bbox)
            out.append(block)
    return out


def grid_to_table(
    grid: Sequence[Sequence[Optional[str]]],
    *,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    header_rows: int = 1,
) -> Optional[TableBlock]:
    if not grid:
        return None
    rows = len(grid)
    cols = max((len(r) for r in grid), default=0)
    if rows == 0 or cols == 0:
        return None
    cells: List[TableCell] = []
    for r, row in enumerate(grid):
        for c in range(cols):
            raw = row[c] if c < len(row) else None
            text = "" if raw is None else str(raw).strip()
            cells.append(
                TableCell(
                    text=text,
                    row=r,
                    col=c,
                    is_header=r < header_rows,
                )
            )
    return TableBlock(
        rows=rows,
        cols=cols,
        cells=cells,
        header_rows=min(header_rows, rows),
        bbox=bbox,
        has_spans=False,
    )


def table_has_spans(table: TableBlock) -> bool:
    return any(c.rowspan > 1 or c.colspan > 1 for c in table.cells) or table.has_spans


def table_to_markdown(table: TableBlock) -> str:
    if table_has_spans(table):
        return table_to_html(table)
    # Build grid
    grid = [["" for _ in range(table.cols)] for _ in range(table.rows)]
    for c in table.cells:
        if 0 <= c.row < table.rows and 0 <= c.col < table.cols:
            grid[c.row][c.col] = _escape_md_cell(c.text)
    if not grid:
        return ""
    lines = []
    header = grid[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in grid[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def table_to_html(table: TableBlock) -> str:
    # Place cells considering rowspan/colspan
    occupied = set()
    grid_cells = {(c.row, c.col): c for c in table.cells}
    parts = ["<table>"]
    for r in range(table.rows):
        parts.append("<tr>")
        c = 0
        while c < table.cols:
            if (r, c) in occupied:
                c += 1
                continue
            cell = grid_cells.get((r, c))
            if cell is None:
                parts.append("<td></td>")
                c += 1
                continue
            tag = "th" if cell.is_header else "td"
            attrs = ""
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
                for rr in range(r, r + cell.rowspan):
                    for cc in range(c, c + max(cell.colspan, 1)):
                        if (rr, cc) != (r, c):
                            occupied.add((rr, cc))
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
                for cc in range(c + 1, c + cell.colspan):
                    occupied.add((r, cc))
            parts.append(f"<{tag}{attrs}>{_escape_html(cell.text)}</{tag}>")
            c += max(cell.colspan, 1)
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def parse_markdown_table(md: str) -> Optional[TableBlock]:
    lines = [ln.strip() for ln in md.strip().splitlines() if ln.strip()]
    if len(lines) < 2 or "|" not in lines[0]:
        return None
    rows_raw = []
    for i, ln in enumerate(lines):
        if re.match(r"^\|?\s*:?-{3,}", ln.replace("|", " ").strip() if False else ln):
            # separator row
            if re.match(r"^\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?$", ln):
                continue
        if ln.count("|") < 2:
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        # skip separator-looking rows
        if all(re.match(r"^:?-{3,}:?$", c or "") for c in cells):
            continue
        rows_raw.append(cells)
    return grid_to_table(rows_raw)


def parse_html_table(html: str) -> Optional[TableBlock]:
    # Minimal HTML table parser for round-trip tests (no full HTML engine).
    row_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
    cell_pat = re.compile(
        r"<(td|th)([^>]*)>(.*?)</\1>", re.I | re.S
    )
    rows = row_pat.findall(html)
    if not rows:
        return None
    cells: List[TableCell] = []
    max_cols = 0
    for r, row_html in enumerate(rows):
        col = 0
        for tag, attrs, content in cell_pat.findall(row_html):
            rowspan = _attr_int(attrs, "rowspan", 1)
            colspan = _attr_int(attrs, "colspan", 1)
            text = re.sub(r"<[^>]+>", "", content).strip()
            text = _unescape_html(text)
            cells.append(
                TableCell(
                    text=text,
                    row=r,
                    col=col,
                    rowspan=rowspan,
                    colspan=colspan,
                    is_header=tag.lower() == "th",
                )
            )
            col += colspan
        max_cols = max(max_cols, col)
    has_spans = any(c.rowspan > 1 or c.colspan > 1 for c in cells)
    return TableBlock(
        rows=len(rows),
        cols=max_cols,
        cells=cells,
        header_rows=1 if any(c.is_header for c in cells) else 0,
        has_spans=has_spans,
    )


def _attr_int(attrs: str, name: str, default: int) -> int:
    m = re.search(rf'{name}\s*=\s*"(\d+)"', attrs, re.I)
    return int(m.group(1)) if m else default


def _escape_md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _unescape_html(text: str) -> str:
    return (
        text.replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
