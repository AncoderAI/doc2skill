"""Offline figure/table describe handshake: export records, merge VLM results.

No network. Multimodal recognition happens outside this process; this module
only serializes records and writes returned descriptions back onto existing
FigureBlock fields (description / entities / relations / chart_data / round_trip).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .assemble import assemble_markdown
from .ir import Block, BlockType, load_document_ir
from .tables import table_has_spans, table_to_html, table_to_markdown

CONTEXT_LIMIT = 400
VALID_ROUND_TRIP = frozenset({"reproducible", "partial", "not_reproducible"})
APPLY_TYPES = frozenset({BlockType.FIGURE, BlockType.TABLE})


def export_requests(
    bundle_dir: Path,
    *,
    include_tables: bool = False,
    pending_only: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Read `<bundle>/document.ir.json` and emit one record per figure (and table).

    ``pending_only`` skips blocks already marked ``description_source == "vlm"``.
    ``limit`` truncates to the first N records after ``(page, block_id)`` sort.
    """
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
    bundle_dir = Path(bundle_dir)
    ir = load_document_ir(bundle_dir / "document.ir.json")
    wanted = {BlockType.FIGURE}
    if include_tables:
        wanted.add(BlockType.TABLE)

    indexed = [(i, b) for i, b in enumerate(ir.blocks) if b.type in wanted]
    if pending_only:
        indexed = [(i, b) for i, b in indexed if not _is_vlm_described(b)]
    indexed.sort(key=lambda item: (item[1].page, item[1].block_id))
    if limit is not None:
        indexed = indexed[:limit]

    records: List[Dict[str, Any]] = []
    for index, block in indexed:
        if block.type == BlockType.FIGURE:
            records.append(_figure_request(bundle_dir, ir.blocks, index, block))
        else:
            records.append(_table_request(ir.blocks, index, block))
    return records


def describe_status(bundle_dir: Path) -> dict:
    """Read-only figure/table describe progress. Does not write any files."""
    bundle_dir = Path(bundle_dir)
    ir = load_document_ir(bundle_dir / "document.ir.json")
    total_figures = 0
    described_figures = 0
    total_tables = 0
    described_tables = 0
    for block in ir.blocks:
        if block.type == BlockType.FIGURE:
            total_figures += 1
            if _is_vlm_described(block):
                described_figures += 1
        elif block.type == BlockType.TABLE:
            total_tables += 1
            if _is_vlm_described(block):
                described_tables += 1
    pending_figures = total_figures - described_figures
    pending_tables = total_tables - described_tables
    return {
        "total_figures": total_figures,
        "described_figures": described_figures,
        "pending_figures": pending_figures,
        "total_tables": total_tables,
        "described_tables": described_tables,
        "pending_tables": pending_tables,
        "done": pending_figures == 0 and pending_tables == 0,
    }


def merge_descriptions(
    bundle_dir: Path, records: list[dict], *, strict: bool = False
) -> dict:
    """Write VLM descriptions back onto IR FigureBlock fields and reassemble markdown.

    ``strict`` is for the caller (CLI exit 2 when unknown_ids / rejected are nonempty).
    """
    bundle_dir = Path(bundle_dir)
    ir = load_document_ir(bundle_dir / "document.ir.json")
    by_id = {b.block_id: b for b in ir.blocks}

    unknown_ids: List[str] = []
    rejected = {"empty": 0, "bad_round_trip": 0, "wrong_type": 0}
    described = 0
    described_figures = 0
    described_tables = 0
    skipped_already_described = 0

    for rec in records:
        block_id = rec.get("block_id")
        block = by_id.get(block_id) if block_id else None
        if block is None:
            unknown_ids.append(block_id if block_id else "")
            continue
        description = rec.get("description")
        if description is None or not str(description).strip():
            rejected["empty"] += 1
            continue
        if rec.get("round_trip") not in VALID_ROUND_TRIP:
            rejected["bad_round_trip"] += 1
            continue
        if block.type not in APPLY_TYPES:
            rejected["wrong_type"] += 1
            continue
        if block.type == BlockType.FIGURE and block.figure is None:
            rejected["wrong_type"] += 1
            continue
        if block.type == BlockType.TABLE and block.table is None:
            rejected["wrong_type"] += 1
            continue
        if (block.meta or {}).get("description_source") == "vlm":
            skipped_already_described += 1
            continue
        _apply_record(block, rec)
        described += 1
        if block.type == BlockType.FIGURE:
            described_figures += 1
        else:
            described_tables += 1

    total_figures = sum(1 for b in ir.blocks if b.type == BlockType.FIGURE)
    total_tables = sum(1 for b in ir.blocks if b.type == BlockType.TABLE)
    report = {
        "total_figures": total_figures,
        "described_figures": described_figures,
        "total_tables": total_tables,
        "described_tables": described_tables,
        "described": described,
        "unknown_ids": unknown_ids,
        "rejected": rejected,
        "skipped_already_described": skipped_already_described,
    }

    md = assemble_markdown(ir)
    (bundle_dir / "document.md").write_text(md, encoding="utf-8")
    ir.to_json(bundle_dir / "document.ir.json")
    (bundle_dir / "describe-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def read_jsonl(path: Path) -> list[dict]:
    records: List[Dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        records.append(json.loads(stripped))
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records),
        encoding="utf-8",
    )


def _is_vlm_described(block: Block) -> bool:
    return (block.meta or {}).get("description_source") == "vlm"


def _figure_request(
    bundle_dir: Path, blocks: List[Block], index: int, block: Block
) -> Dict[str, Any]:
    fig = block.figure
    assert fig is not None
    before, after = _same_page_text_context(blocks, index)
    return {
        "block_id": block.block_id,
        "kind": "figure",
        "page": block.page,
        "asset_path": _bundle_relative_asset(bundle_dir, fig.asset_path),
        "caption": fig.caption,
        "ocr_labels": list(fig.ocr_labels or []),
        "category": fig.category,
        "bbox": _bbox_list(fig.bbox or block.bbox),
        "context_before": before,
        "context_after": after,
    }


def _table_request(blocks: List[Block], index: int, block: Block) -> Dict[str, Any]:
    table = block.table
    assert table is not None
    before, after = _same_page_text_context(blocks, index)
    if table_has_spans(table):
        table_md = table_to_html(table)
    else:
        table_md = table_to_markdown(table)
    return {
        "block_id": block.block_id,
        "kind": "table",
        "page": block.page,
        "caption": table.caption,
        "bbox": _bbox_list(table.bbox or block.bbox),
        "context_before": before,
        "context_after": after,
        "table_markdown": table_md,
    }


def _same_page_text_context(
    blocks: List[Block], index: int, limit: int = CONTEXT_LIMIT
) -> Tuple[str, str]:
    page = blocks[index].page
    before = ""
    for j in range(index - 1, -1, -1):
        other = blocks[j]
        if other.page != page:
            continue
        if other.type == BlockType.TEXT:
            text = (other.text or "").strip()
            if text:
                before = text
                break
    after = ""
    for j in range(index + 1, len(blocks)):
        other = blocks[j]
        if other.page != page:
            continue
        if other.type == BlockType.TEXT:
            text = (other.text or "").strip()
            if text:
                after = text
                break
    if len(before) > limit:
        before = before[-limit:]
    if len(after) > limit:
        after = after[:limit]
    return before, after


def _bundle_relative_asset(bundle_dir: Path, asset_path: str) -> str:
    raw = (asset_path or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute():
        return path.resolve().relative_to(bundle_dir.resolve()).as_posix()
    return Path(raw).as_posix()


def _bbox_list(bbox: Optional[Tuple[float, ...]]) -> Optional[List[float]]:
    if not bbox:
        return None
    return [float(x) for x in bbox]


def _apply_record(block: Block, rec: Dict[str, Any]) -> None:
    description = str(rec["description"])
    entities = list(rec["entities"]) if rec.get("entities") is not None else []
    relations = list(rec["relations"]) if rec.get("relations") is not None else []
    chart_data = rec.get("chart_data")
    round_trip = rec["round_trip"]
    if block.type == BlockType.FIGURE:
        fig = block.figure
        assert fig is not None
        fig.description = description
        fig.entities = entities
        fig.relations = relations
        fig.chart_data = chart_data
        fig.round_trip = round_trip
    else:
        block.meta["description"] = description
        block.meta["entities"] = entities
        block.meta["relations"] = relations
        block.meta["chart_data"] = chart_data
        block.meta["round_trip"] = round_trip
    block.meta["description_source"] = "vlm"
    block.meta["description_model"] = rec.get("model")
    block.meta["described_at"] = rec.get("generated_at")
