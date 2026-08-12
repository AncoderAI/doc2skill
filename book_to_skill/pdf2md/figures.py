"""Figure / formula asset helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image

from .ir import BBox, FigureBlock, FormulaBlock


_FORMULA_HINT = re.compile(
    r"(\$[^$]+\$)|(\\frac\{)|(\\sum)|(\\int)|(\\sqrt)|([∫∑∏√≈≠≤≥±×÷])"
)


def crop_and_save(
    page_image: Image.Image,
    bbox_pts: BBox,
    page_size_pts: Tuple[float, float],
    dest: Path,
    *,
    dpi: int = 300,
) -> Path:
    """Crop page image using PDF-point bbox and save PNG."""
    scale = dpi / 72.0
    x0, y0, x1, y1 = bbox_pts
    # PDF origin bottom-left; PIL top-left
    page_w, page_h = page_size_pts
    left = int(x0 * scale)
    right = int(x1 * scale)
    top = int((page_h - y1) * scale)
    bottom = int((page_h - y0) * scale)
    left, right = max(0, min(left, right)), max(left, right, left + 1)
    top, bottom = max(0, min(top, bottom)), max(top, bottom, top + 1)
    crop = page_image.crop((left, top, right, bottom))
    dest.parent.mkdir(parents=True, exist_ok=True)
    crop.save(dest, format="PNG", dpi=(dpi, dpi))
    return dest


def figure_from_image(
    asset_path: str,
    *,
    bbox: Optional[BBox] = None,
    ocr_text: str = "",
    prompt: str = "",
) -> FigureBlock:
    labels = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()][:40]
    category = "diagram" if labels else "photo"
    description = None
    if prompt and labels:
        description = "Labels: " + "; ".join(labels[:12])
    elif labels:
        description = "OCR labels present"
    return FigureBlock(
        asset_path=asset_path,
        category=category,
        ocr_labels=labels,
        description=description,
        bbox=bbox,
        round_trip="not_applicable" if category == "photo" else "structured",
    )


def detect_formula_candidates(text: str) -> List[str]:
    hits = []
    for m in _FORMULA_HINT.finditer(text):
        hits.append(m.group(0))
    return hits


def normalize_latex(latex: str) -> Tuple[str, List[str]]:
    s = latex.strip()
    if s.startswith("$$") and s.endswith("$$"):
        s = s[2:-2].strip()
    if s.startswith("$") and s.endswith("$"):
        s = s[1:-1].strip()
    tokens = re.findall(r"\\[a-zA-Z]+|[{}]|[A-Za-z]+|\d+|[^\s]", s)
    return s, tokens


def formula_failure(asset_path: Optional[str], reason: str) -> FormulaBlock:
    return FormulaBlock(
        latex=None,
        tokens=[],
        confidence=0.0,
        asset_path=asset_path,
        failed=True,
        failure_reason=reason,
    )


def formula_from_latex(latex: str, *, confidence: float = 0.5, asset_path: Optional[str] = None) -> FormulaBlock:
    norm, tokens = normalize_latex(latex)
    if not norm:
        return formula_failure(asset_path, "empty latex")
    return FormulaBlock(
        latex=norm,
        tokens=tokens,
        confidence=confidence,
        asset_path=asset_path,
        failed=False,
    )
