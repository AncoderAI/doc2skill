"""Declarative conversion profiles (optimizer may only edit these in v1)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConvertProfile:
    name: str = "auto"
    dpi: int = 300
    ocr_lang: str = "eng"
    ocr_lang_fallback: List[str] = field(default_factory=lambda: ["eng", "deu+eng"])
    ocr_psm: int = 3
    force_ocr_min_chars: int = 40
    force_ocr_garbage_ratio: float = 0.35
    watermark_page_fraction: float = 0.5
    max_repeated_line_ratio: float = 0.20
    min_nonempty_page_ratio: float = 0.98
    table_mode: str = "accurate"  # fast|accurate
    enable_formulas: bool = True
    enable_figures: bool = True
    enable_charts: bool = True
    enable_ocr_tables: bool = False  # scanned-page OCR word-box → projection grid tables
    html_tables_on_span: bool = True
    figure_caption_prompt: str = (
        "Describe the figure briefly for retrieval; list visible labels."
    )
    # When set, only convert these 1-based pages (debug / sentinel).
    page_filter: Optional[List[int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConvertProfile":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


PROFILES: Dict[str, ConvertProfile] = {
    "fast": ConvertProfile(
        name="fast",
        dpi=150,
        table_mode="fast",
        enable_formulas=False,
        enable_figures=True,
        enable_charts=False,
        ocr_psm=6,
    ),
    "accurate": ConvertProfile(
        name="accurate",
        dpi=300,
        table_mode="accurate",
        enable_formulas=True,
        enable_figures=True,
        enable_charts=True,
        enable_ocr_tables=True,
        ocr_psm=3,
    ),
    "auto": ConvertProfile(name="auto"),
}


def resolve_profile(name: str, overrides: Optional[Dict[str, Any]] = None) -> ConvertProfile:
    base = deepcopy(PROFILES.get(name) or PROFILES["auto"])
    base.name = name if name in PROFILES else "auto"
    if overrides:
        for k, v in overrides.items():
            if hasattr(base, k):
                setattr(base, k, v)
    return base


def default_ocr_lang_for_path(path: str) -> str:
    lower = path.lower()
    if "siemens" in lower or "sn 29500" in lower:
        return "deu+eng"
    if "iec" in lower:
        return "eng"
    return "eng"
