"""High-fidelity PDF → Markdown IR types and helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

SCHEMA_VERSION = "1.0.0"

BBox = Tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points


class PageType(str, Enum):
    NATIVE_TEXT = "native-text"
    SCANNED = "scanned"
    IMAGE_BASED = "image-based"
    MIXED = "mixed"
    BROKEN_ENCODING = "broken-encoding"


class BlockType(str, Enum):
    HEADING = "heading"
    TEXT = "text"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    CODE = "code"


@dataclass
class TableCell:
    text: str
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    unit: Optional[str] = None


@dataclass
class TableBlock:
    rows: int
    cols: int
    cells: List[TableCell]
    header_rows: int = 1
    caption: Optional[str] = None
    footnotes: List[str] = field(default_factory=list)
    bbox: Optional[BBox] = None
    has_spans: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "header_rows": self.header_rows,
            "caption": self.caption,
            "footnotes": list(self.footnotes),
            "bbox": list(self.bbox) if self.bbox else None,
            "has_spans": self.has_spans,
            "cells": [asdict(c) for c in self.cells],
        }


@dataclass
class FigureBlock:
    asset_path: str
    category: str = "unknown"  # photo|chart|diagram|flowchart|other
    caption: Optional[str] = None
    ocr_labels: List[str] = field(default_factory=list)
    description: Optional[str] = None
    entities: List[str] = field(default_factory=list)
    relations: List[Dict[str, str]] = field(default_factory=list)
    chart_data: Optional[Dict[str, Any]] = None
    bbox: Optional[BBox] = None
    round_trip: str = "not_applicable"  # photos

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.bbox:
            d["bbox"] = list(self.bbox)
        return d


@dataclass
class FormulaBlock:
    latex: Optional[str]
    tokens: List[str] = field(default_factory=list)
    confidence: float = 0.0
    asset_path: Optional[str] = None
    bbox: Optional[BBox] = None
    failed: bool = False
    failure_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.bbox:
            d["bbox"] = list(self.bbox)
        return d


@dataclass
class Block:
    block_id: str
    type: BlockType
    page: int
    text: str = ""
    level: Optional[int] = None  # heading level
    bbox: Optional[BBox] = None
    table: Optional[TableBlock] = None
    figure: Optional[FigureBlock] = None
    formula: Optional[FormulaBlock] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "block_id": self.block_id,
            "type": self.type.value,
            "page": self.page,
            "text": self.text,
            "level": self.level,
            "bbox": list(self.bbox) if self.bbox else None,
            "meta": self.meta,
        }
        if self.table is not None:
            d["table"] = self.table.to_dict()
        if self.figure is not None:
            d["figure"] = self.figure.to_dict()
        if self.formula is not None:
            d["formula"] = self.formula.to_dict()
        return d


@dataclass
class PageInfo:
    page: int  # 1-based
    width: float
    height: float
    rotation: int
    page_type: PageType
    text_layer_chars: int = 0
    ocr_chars: int = 0
    force_ocr: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "page_type": self.page_type.value,
            "text_layer_chars": self.text_layer_chars,
            "ocr_chars": self.ocr_chars,
            "force_ocr": self.force_ocr,
        }


@dataclass
class DocumentIR:
    schema_version: str
    source_path: str
    source_sha256: str
    page_count: int
    pages: List[PageInfo] = field(default_factory=list)
    blocks: List[Block] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    profile: str = "auto"
    engine: str = "local"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "page_count": self.page_count,
            "profile": self.profile,
            "engine": self.engine,
            "warnings": list(self.warnings),
            "pages": [p.to_dict() for p in self.pages],
            "blocks": [b.to_dict() for b in self.blocks],
        }

    def to_json(self, path: Union[str, Path], indent: int = 2) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=indent, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def file_sha256(path: Union[str, Path]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_block_id(page: int, index: int, kind: str) -> str:
    return f"p{page:04d}-{kind}-{index:04d}"


def validate_ir_dict(data: Dict[str, Any]) -> List[str]:
    """Return list of schema problems (empty = ok)."""
    errors: List[str] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version expected {SCHEMA_VERSION}")
    for key in ("source_sha256", "page_count", "pages", "blocks"):
        if key not in data:
            errors.append(f"missing {key}")
    if not isinstance(data.get("pages"), list):
        errors.append("pages must be list")
    if not isinstance(data.get("blocks"), list):
        errors.append("blocks must be list")
    allowed = {t.value for t in BlockType}
    for i, b in enumerate(data.get("blocks") or []):
        if b.get("type") not in allowed:
            errors.append(f"blocks[{i}].type invalid: {b.get('type')}")
        if "block_id" not in b or "page" not in b:
            errors.append(f"blocks[{i}] missing block_id/page")
    return errors


def _bbox_from_json(value: Any) -> Optional[BBox]:
    if not value:
        return None
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def table_block_from_dict(data: Dict[str, Any]) -> TableBlock:
    cells = [
        TableCell(
            text=c.get("text", ""),
            row=int(c.get("row", 0)),
            col=int(c.get("col", 0)),
            rowspan=int(c.get("rowspan", 1)),
            colspan=int(c.get("colspan", 1)),
            is_header=bool(c.get("is_header", False)),
            unit=c.get("unit"),
        )
        for c in (data.get("cells") or [])
    ]
    return TableBlock(
        rows=int(data.get("rows", 0)),
        cols=int(data.get("cols", 0)),
        cells=cells,
        header_rows=int(data.get("header_rows", 1)),
        caption=data.get("caption"),
        footnotes=list(data.get("footnotes") or []),
        bbox=_bbox_from_json(data.get("bbox")),
        has_spans=bool(data.get("has_spans", False)),
    )


def figure_block_from_dict(data: Dict[str, Any]) -> FigureBlock:
    return FigureBlock(
        asset_path=data.get("asset_path") or "",
        category=data.get("category") or "unknown",
        caption=data.get("caption"),
        ocr_labels=list(data.get("ocr_labels") or []),
        description=data.get("description"),
        entities=list(data.get("entities") or []),
        relations=list(data.get("relations") or []),
        chart_data=data.get("chart_data"),
        bbox=_bbox_from_json(data.get("bbox")),
        round_trip=data.get("round_trip") or "not_applicable",
    )


def formula_block_from_dict(data: Dict[str, Any]) -> FormulaBlock:
    return FormulaBlock(
        latex=data.get("latex"),
        tokens=list(data.get("tokens") or []),
        confidence=float(data.get("confidence") or 0.0),
        asset_path=data.get("asset_path"),
        bbox=_bbox_from_json(data.get("bbox")),
        failed=bool(data.get("failed", False)),
        failure_reason=data.get("failure_reason"),
    )


def block_from_dict(data: Dict[str, Any]) -> Block:
    return Block(
        block_id=data["block_id"],
        type=data["type"] if isinstance(data["type"], BlockType) else BlockType(data["type"]),
        page=int(data["page"]),
        text=data.get("text") or "",
        level=data.get("level"),
        bbox=_bbox_from_json(data.get("bbox")),
        table=table_block_from_dict(data["table"]) if data.get("table") else None,
        figure=figure_block_from_dict(data["figure"]) if data.get("figure") else None,
        formula=formula_block_from_dict(data["formula"]) if data.get("formula") else None,
        meta=dict(data.get("meta") or {}),
    )


def page_info_from_dict(data: Dict[str, Any]) -> PageInfo:
    page_type = data.get("page_type", PageType.NATIVE_TEXT)
    if not isinstance(page_type, PageType):
        page_type = PageType(page_type)
    return PageInfo(
        page=int(data["page"]),
        width=float(data.get("width") or 0.0),
        height=float(data.get("height") or 0.0),
        rotation=int(data.get("rotation") or 0),
        page_type=page_type,
        text_layer_chars=int(data.get("text_layer_chars") or 0),
        ocr_chars=int(data.get("ocr_chars") or 0),
        force_ocr=bool(data.get("force_ocr", False)),
    )


def document_ir_from_dict(data: Dict[str, Any]) -> DocumentIR:
    return DocumentIR(
        schema_version=data.get("schema_version") or SCHEMA_VERSION,
        source_path=data.get("source_path") or "",
        source_sha256=data.get("source_sha256") or "",
        page_count=int(data.get("page_count") or 0),
        pages=[page_info_from_dict(p) for p in (data.get("pages") or [])],
        blocks=[block_from_dict(b) for b in (data.get("blocks") or [])],
        warnings=list(data.get("warnings") or []),
        profile=data.get("profile") or "auto",
        engine=data.get("engine") or "local",
    )


def load_document_ir(path: Union[str, Path]) -> DocumentIR:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return document_ir_from_dict(data)
