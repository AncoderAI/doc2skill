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
