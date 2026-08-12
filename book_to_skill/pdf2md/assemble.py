"""Assemble DocumentIR into document.md and asset layout."""

from __future__ import annotations

from pathlib import Path
from typing import List

from .ir import Block, BlockType, DocumentIR
from .tables import table_has_spans, table_to_html, table_to_markdown


def assemble_markdown(ir: DocumentIR) -> str:
    parts: List[str] = []
    current_page = None
    for block in ir.blocks:
        if block.page != current_page:
            current_page = block.page
            parts.append(f"\n<!-- page: {current_page} -->\n")
        parts.append(_render_block(block))
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def _render_block(block: Block) -> str:
    bid = f"<!-- block: {block.block_id} -->"
    if block.type == BlockType.HEADING:
        level = block.level or 2
        level = max(1, min(level, 6))
        return f"{bid}\n{'#' * level} {block.text.strip()}"
    if block.type == BlockType.TABLE and block.table is not None:
        body = (
            table_to_html(block.table)
            if table_has_spans(block.table)
            else table_to_markdown(block.table)
        )
        cap = f"\n*{block.table.caption}*" if block.table.caption else ""
        return f"{bid}\n{body}{cap}"
    if block.type == BlockType.FIGURE and block.figure is not None:
        alt = block.figure.caption or block.figure.description or "figure"
        return f"{bid}\n![{alt}]({block.figure.asset_path})"
    if block.type == BlockType.FORMULA and block.formula is not None:
        if block.formula.failed or not block.formula.latex:
            img = ""
            if block.formula.asset_path:
                img = f"\n![formula]({block.formula.asset_path})"
            return f"{bid}\n<!-- formula_failed: {block.formula.failure_reason} -->{img}"
        return f"{bid}\n$$\n{block.formula.latex}\n$$"
    if block.type == BlockType.LIST:
        return f"{bid}\n{block.text.strip()}"
    if block.type == BlockType.CODE:
        return f"{bid}\n```\n{block.text.rstrip()}\n```"
    return f"{bid}\n{block.text.strip()}"


def write_bundle(ir: DocumentIR, output_dir: Path, quality: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("pages", "figures", "tables", "formulas"):
        (output_dir / "assets" / sub).mkdir(parents=True, exist_ok=True)
    md = assemble_markdown(ir)
    (output_dir / "document.md").write_text(md, encoding="utf-8")
    ir.to_json(output_dir / "document.ir.json")
    import json

    (output_dir / "quality-report.json").write_text(
        json.dumps(quality, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
