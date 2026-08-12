"""Table extraction and Markdown/HTML round-trip helpers."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from .ir import TableBlock, TableCell


def extract_tables_pdfplumber(pdf_path: str, page_index: int) -> List[TableBlock]:
    """Extract tables from a single 0-based page via pdfplumber."""
    import pdfplumber

    out: List[TableBlock] = []
    with pdfplumber.open(pdf_path) as pdf:
        if page_index < 0 or page_index >= len(pdf.pages):
            return out
        page = pdf.pages[page_index]
        found = page.find_tables() or []
        for t in found:
            data = t.extract() or []
            block = grid_to_table(data, bbox=tuple(t.bbox) if t.bbox else None)
            if block is not None:
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
