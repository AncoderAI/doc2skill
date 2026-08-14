"""Figure / formula asset helpers and B-stage detectors."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageStat

from .ir import BBox, FigureBlock, FormulaBlock

FULL_PAGE_AREA_RATIO = 0.92
TOO_SMALL_AREA_RATIO = 0.005
SEPARATOR_WIDTH_RATIO = 0.85
SEPARATOR_MAX_HEIGHT_PT = 3.0
TABLE_IOU_DROP = 0.3
VECTOR_CLUSTER_GAP_PT = 14.0
MIN_CLUSTER_AREA_PT = 800.0
INK_RATIO_MIN = 0.05
INK_RATIO_MAX = 0.40
WORD_DENSITY_RATIO_MAX = 0.4
REPEATED_DECORATION_MIN_PAGES = 3
MIN_ASSET_SIDE = 32
REGION_MIN_SIDE_PT = 36.0
FORMULA_MIN_CLASSES = 2

_FORMULA_HINT = re.compile(
    r"(\$[^$]+\$)|(\\frac\{)|(\\sum)|(\\int)|(\\sqrt)|([∫∑∏√≈≠≤≥±×÷])"
)

_GREEK_RE = re.compile(
    r"[αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩλμπσΔΣΩ]"
)
_OPERATOR_RE = re.compile(r"[=≠≈≡≤≥<>±×÷·⋅∑∏∫√∞]|\\(?:frac|sum|int|sqrt|times|leq|geq|neq)")
_SCRIPT_RE = re.compile(
    r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂₃₄₅₆₇₈₉₊₋]|[A-Za-zα-ωΑ-Ωλμ]\d{1,2}\b|_\d|[\^_]"
)
_EQ_NUMBER_RE = re.compile(r"\(\d{1,3}\)\s*$")
_CAPTION_RE = re.compile(
    r"(?i)\b(?:Figure|Fig\.|Bild|Abbildung)\s*(\d+)\b"
)


@dataclass
class FigureCandidate:
    bbox: BBox
    route: str  # vector|raster|region
    page: int
    dropped: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FormulaCandidate:
    page: int
    line: str
    bbox: Optional[BBox]
    features: Dict[str, float]
    classes_hit: List[str]
    score: float
    passed: bool
    threshold_rule: str


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


def ocr_figure_asset(
    image: Image.Image,
    *,
    lang: str,
    psm: int,
    dpi: int,
) -> Tuple[str, Optional[str]]:
    """OCR one figure crop. Returns (text, warning_or_none).

    Tesseract missing → empty text + warning (does not raise). A tesseract
    failure while the binary is present still raises — that is not a silent
    skip.
    """
    from .ocr import ocr_image, tesseract_available

    if not tesseract_available():
        return "", "figure OCR skipped: tesseract not available"
    return ocr_image(image, lang=lang, psm=psm, dpi=dpi), None


def figure_from_image(
    asset_path: str,
    *,
    bbox: Optional[BBox] = None,
    ocr_text: str = "",
    prompt: str = "",
    caption: Optional[str] = None,
    category: Optional[str] = None,
) -> FigureBlock:
    labels = [ln.strip() for ln in ocr_text.splitlines() if ln.strip()][:40]
    cat = category or ("diagram" if labels else "photo")
    description = None
    if prompt and labels:
        description = "Labels: " + "; ".join(labels[:12])
    elif labels:
        description = "OCR labels present"
    return FigureBlock(
        asset_path=asset_path,
        category=cat,
        caption=caption,
        ocr_labels=labels,
        description=description,
        bbox=bbox,
        round_trip="pending",
    )


def detect_formula_candidates(text: str) -> List[str]:
    """Legacy hint detector (kept for tests); prefer score_formula_line."""
    hits = []
    for m in _FORMULA_HINT.finditer(text):
        hits.append(m.group(0))
    return hits


def score_formula_line(line: str) -> FormulaCandidate:
    """Score one line by math-layout feature classes. Conservative: ≥2 classes."""
    text = (line or "").strip()
    features = {
        "greek": float(len(_GREEK_RE.findall(text))),
        "operators": float(len(_OPERATOR_RE.findall(text))),
        "scripts": float(len(_SCRIPT_RE.findall(text))),
        "eq_number": 1.0 if _EQ_NUMBER_RE.search(text) else 0.0,
    }
    classes_hit = [k for k, v in features.items() if v > 0]
    # Conservative gates (documented in formula_candidates.json):
    # A) greek + operator (λ = …)
    # B) ≥2 operators with ×/· or trailing (N) — covers IEC table-cell laws
    #    like "=0.024×D (1)" where λ sits in a separate header cell.
    # Scripts/(N) alone never pass; bare '=' OCR noise never passes.
    has_mul = ("×" in text) or ("·" in text) or ("⋅" in text)
    passed = (
        features["greek"] > 0 and features["operators"] > 0
    ) or (
        features["operators"] >= 2 and (features["eq_number"] > 0 or has_mul)
    )
    score = sum(features.values())
    return FormulaCandidate(
        page=0,
        line=text,
        bbox=None,
        features=features,
        classes_hit=classes_hit,
        score=score,
        passed=passed,
        threshold_rule=(
            "passed_if (greek>0 and operators>0) or "
            "(operators>=2 and (eq_number>0 or has_mul)); "
            f"classes_hit={classes_hit}; has_mul={has_mul}"
        ),
    )


def detect_formula_lines(
    lines: Sequence[Tuple[str, Optional[BBox]]],
    *,
    page: int,
) -> List[FormulaCandidate]:
    out: List[FormulaCandidate] = []
    for text, bbox in lines:
        cand = score_formula_line(text)
        cand.page = page
        cand.bbox = bbox
        out.append(cand)
    return out


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


def formula_from_latex(
    latex: str, *, confidence: float = 0.5, asset_path: Optional[str] = None
) -> FormulaBlock:
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


def bbox_area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def bbox_iou(a: BBox, b: BBox) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    union = bbox_area(a) + bbox_area(b) - inter
    return inter / union if union > 0 else 0.0


def apply_area_gates(
    cand: FigureCandidate, page_w: float, page_h: float
) -> FigureCandidate:
    page_area = max(page_w * page_h, 1.0)
    ratio = bbox_area(cand.bbox) / page_area
    cand.extra["area_ratio"] = ratio
    if ratio >= FULL_PAGE_AREA_RATIO:
        cand.dropped = "full_page"
    elif ratio < TOO_SMALL_AREA_RATIO:
        cand.dropped = "too_small"
    return cand


def is_solid_or_tiny(image: Image.Image) -> Optional[str]:
    if image.width < MIN_ASSET_SIDE or image.height < MIN_ASSET_SIDE:
        return "tiny_asset"
    gray = image.convert("L")
    stat = ImageStat.Stat(gray)
    # near-constant luminance → blank / solid fill
    if stat.extrema and (stat.extrema[0][1] - stat.extrema[0][0]) < 3:
        return "solid_color"
    return None


def image_fingerprint(image: Image.Image) -> str:
    small = image.convert("L").resize((32, 32))
    return hashlib.sha256(small.tobytes()).hexdigest()[:16]


def _obj_bbox(obj: Dict[str, Any]) -> Optional[BBox]:
    try:
        x0 = float(obj["x0"])
        x1 = float(obj["x1"])
        # pdfplumber: y0/y1 are bottom-up; top/bottom also available
        y0 = float(obj["y0"])
        y1 = float(obj["y1"])
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        return (x0, y0, x1, y1)
    except (KeyError, TypeError, ValueError):
        return None


def _is_separator(bbox: BBox, page_w: float) -> bool:
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return w >= SEPARATOR_WIDTH_RATIO * page_w and h <= SEPARATOR_MAX_HEIGHT_PT


def _overlaps_table(bbox: BBox, table_bboxes: Sequence[BBox]) -> bool:
    for tb in table_bboxes:
        if bbox_iou(bbox, tb) >= TABLE_IOU_DROP:
            return True
        # also drop if mostly contained in a table
        inter_x0 = max(bbox[0], tb[0])
        inter_y0 = max(bbox[1], tb[1])
        inter_x1 = min(bbox[2], tb[2])
        inter_y1 = min(bbox[3], tb[3])
        inter = max(0.0, inter_x1 - inter_x0) * max(0.0, inter_y1 - inter_y0)
        if inter / max(bbox_area(bbox), 1.0) >= 0.6:
            return True
    return False


def _cluster_bboxes(boxes: List[BBox], gap: float) -> List[BBox]:
    if not boxes:
        return []
    # Union-find by expanded-bbox overlap
    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    expanded = [
        (b[0] - gap, b[1] - gap, b[2] + gap, b[3] + gap) for b in boxes
    ]
    for i in range(n):
        for j in range(i + 1, n):
            if bbox_iou(expanded[i], expanded[j]) > 0 or _aabb_overlap(
                expanded[i], expanded[j]
            ):
                union(i, j)

    groups: Dict[int, List[BBox]] = {}
    for i, b in enumerate(boxes):
        groups.setdefault(find(i), []).append(b)
    clusters: List[BBox] = []
    for members in groups.values():
        x0 = min(m[0] for m in members)
        y0 = min(m[1] for m in members)
        x1 = max(m[2] for m in members)
        y1 = max(m[3] for m in members)
        clusters.append((x0, y0, x1, y1))
    return clusters


def _aabb_overlap(a: BBox, b: BBox) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _mupdf_rect_to_pdf_bbox(
    rect: Sequence[float], page_h: float
) -> Optional[BBox]:
    """PyMuPDF rect (top-left origin) → PDF bottom-left bbox."""
    try:
        x0, y0_top, x1, y1_top = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except (TypeError, ValueError, IndexError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1_top < y0_top:
        y0_top, y1_top = y1_top, y0_top
    y0 = page_h - y1_top
    y1 = page_h - y0_top
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def detect_vector_figures(
    pdf_path: str,
    page_index: int,
    page_no: int,
    page_w: float,
    page_h: float,
    table_bboxes: Sequence[BBox],
) -> List[FigureCandidate]:
    """Cluster PyMuPDF ``get_drawings()`` primitives; drop tables/separators.

    Product extraction path — deterministic geometry only (no layout ML).
    """
    from .handles import get_fitz

    cands: List[FigureCandidate] = []
    doc = get_fitz(pdf_path)
    if page_index < 0 or page_index >= len(doc):
        return cands
    page = doc[page_index]
    # Prefer mediabox height for PDF-space conversion
    page_h_m = float(page.rect.height) or page_h
    page_w_m = float(page.rect.width) or page_w
    primitives: List[BBox] = []
    dropped_seps = 0
    for d in page.get_drawings() or []:
        rect = d.get("rect")
        if rect is None:
            continue
        bb = _mupdf_rect_to_pdf_bbox(rect, page_h_m)
        if bb is None:
            continue
        if _is_separator(bb, page_w_m):
            dropped_seps += 1
            continue
        if bbox_area(bb) < 1.0 and (bb[3] - bb[1]) <= 1.0:
            continue
        primitives.append(bb)
    clusters = _cluster_bboxes(primitives, VECTOR_CLUSTER_GAP_PT)
    for cl in clusters:
        cand = FigureCandidate(bbox=cl, route="vector", page=page_no)
        cand.extra["n_primitives_page"] = len(primitives)
        cand.extra["separators_dropped"] = dropped_seps
        cand.extra["vector_source"] = "pymupdf_get_drawings"
        if bbox_area(cl) < MIN_CLUSTER_AREA_PT:
            cand.dropped = "too_small"
            cands.append(cand)
            continue
        if _overlaps_table(cl, table_bboxes):
            cand.dropped = "table_overlap"
            cands.append(cand)
            continue
        apply_area_gates(cand, page_w, page_h)
        cands.append(cand)
    return cands


def detect_raster_figures(
    pdf_path: str,
    page_index: int,
    page_no: int,
    page_w: float,
    page_h: float,
) -> List[FigureCandidate]:
    """Embedded XObject images via pdfplumber; full-page / tiny dropped by gates."""
    from .handles import get_plumber_page

    cands: List[FigureCandidate] = []
    page = get_plumber_page(pdf_path, page_index)
    if page is None:
        return cands
    for im in page.images or []:
        bb = _obj_bbox(im)
        if bb is None:
            continue
        src = im.get("srcsize") or ()
        cand = FigureCandidate(
            bbox=bb,
            route="raster",
            page=page_no,
            extra={
                "srcsize": list(src) if src else [],
                "name": im.get("name"),
            },
        )
        # Pixel-size floor: IEC logos are 116×116 — below any real figure.
        if (
            isinstance(src, (list, tuple))
            and len(src) >= 2
            and max(int(src[0]), int(src[1])) <= 128
        ):
            cand.dropped = "too_small"
            cand.extra["area_ratio"] = bbox_area(bb) / max(page_w * page_h, 1.0)
            cands.append(cand)
            continue
        apply_area_gates(cand, page_w, page_h)
        cands.append(cand)
    return cands


def _binary_ink_mask(image: Image.Image, threshold: int = 240):
    import numpy as np

    gray = np.asarray(image.convert("L"), dtype="uint8")
    # ink = darker than threshold
    return gray < threshold


def _projection_blocks(mask) -> List[Tuple[int, int, int, int]]:
    """Return pixel bboxes (x0,y0,x1,y1) via row/col projection gaps. numpy only."""
    h, w = mask.shape
    # Coarse downsample to keep runtime sane on 300dpi pages
    step = 4
    small = mask[::step, ::step]
    sh, sw = small.shape
    row_ink = small.any(axis=1)
    # Find contiguous row bands
    bands: List[Tuple[int, int]] = []
    i = 0
    while i < sh:
        if not row_ink[i]:
            i += 1
            continue
        j = i + 1
        while j < sh and row_ink[j]:
            j += 1
        # allow small gaps inside a band
        while j < sh - 1 and (not row_ink[j]) and row_ink[j + 1]:
            # bridge 1-row gap
            j += 2
            while j < sh and row_ink[j]:
                j += 1
        bands.append((i, j))
        i = j

    boxes: List[Tuple[int, int, int, int]] = []
    for r0, r1 in bands:
        band = small[r0:r1]
        col_ink = band.any(axis=0)
        c = 0
        while c < sw:
            if not col_ink[c]:
                c += 1
                continue
            d = c + 1
            while d < sw and col_ink[d]:
                d += 1
            # map back to full-res px
            x0, x1 = c * step, min(w, d * step)
            y0, y1 = r0 * step, min(h, r1 * step)
            if (x1 - x0) >= 40 and (y1 - y0) >= 40:
                boxes.append((x0, y0, x1, y1))
            c = d
    return boxes


def detect_region_figures(
    page_image: Image.Image,
    page_no: int,
    page_w: float,
    page_h: float,
    word_boxes: Sequence[Tuple[BBox, str]],
    *,
    dpi: int = 300,
) -> List[FigureCandidate]:
    """Ink-dense regions whose word density is << page body average.

    「无词」用相对判据：bbox 词密度 < 0.4 × 本页正文区平均词密度。
    ink_ratio 门：5%–40%（白纸 ink≈0 → 自动淘汰）。
    """
    mask = _binary_ink_mask(page_image)
    h_px, w_px = mask.shape
    scale = dpi / 72.0
    page_area = max(page_w * page_h, 1.0)

    body = (
        page_w * 0.05,
        page_h * 0.05,
        page_w * 0.95,
        page_h * 0.95,
    )
    body_area = bbox_area(body)
    body_words = sum(1 for wb, _ in word_boxes if _aabb_overlap(wb, body))
    body_density = body_words / max(body_area, 1.0)

    px_boxes = _projection_blocks(mask)
    raw_boxes: List[BBox] = []
    for x0_px, y0_px, x1_px, y1_px in px_boxes:
        x0 = x0_px / scale
        x1 = x1_px / scale
        y1 = page_h - (y0_px / scale)
        y0 = page_h - (y1_px / scale)
        if y1 < y0:
            y0, y1 = y1, y0
        raw_boxes.append((x0, y0, x1, y1))

    clusters = _cluster_bboxes(raw_boxes, gap=20.0)
    cands: List[FigureCandidate] = []
    for cl in clusters:
        w = cl[2] - cl[0]
        h = cl[3] - cl[1]
        if w < REGION_MIN_SIDE_PT or h < REGION_MIN_SIDE_PT:
            continue
        left = max(0, int(cl[0] * scale))
        right = min(w_px, int(cl[2] * scale))
        top = max(0, int((page_h - cl[3]) * scale))
        bottom = min(h_px, int((page_h - cl[1]) * scale))
        if right - left < 2 or bottom - top < 2:
            continue
        region_mask = mask[top:bottom, left:right]
        ink_ratio = float(region_mask.mean()) if region_mask.size else 0.0
        words_inside = sum(1 for wb, _ in word_boxes if _aabb_overlap(wb, cl))
        dens = words_inside / max(bbox_area(cl), 1.0)
        if body_density > 1e-12:
            dens_ratio = dens / body_density
        else:
            dens_ratio = 0.0 if words_inside == 0 else 999.0
        cand = FigureCandidate(
            bbox=cl,
            route="region",
            page=page_no,
            extra={
                "ink_ratio": ink_ratio,
                "word_count": words_inside,
                "word_density": dens,
                "body_word_density": body_density,
                "word_density_ratio": dens_ratio,
            },
        )
        if not (INK_RATIO_MIN <= ink_ratio <= INK_RATIO_MAX):
            cand.dropped = "ink_ratio"
            cands.append(cand)
            continue
        if dens_ratio >= WORD_DENSITY_RATIO_MAX:
            cand.dropped = "word_density"
            cands.append(cand)
            continue
        apply_area_gates(cand, page_w, page_h)
        if cand.dropped is None and bbox_area(cl) / page_area >= FULL_PAGE_AREA_RATIO:
            cand.dropped = "full_page"
        cands.append(cand)
    return cands


def bind_caption(bbox: BBox, caption_lines: Sequence[Dict[str, Any]]) -> Optional[str]:
    """Attach nearest Figure/Bild caption below/above the bbox."""
    if not caption_lines:
        return None
    best = None
    for cap in caption_lines:
        label = cap.get("label") or cap.get("line") or ""
        y = float(cap.get("y") or 0.0)
        # prefer captions just below the figure (smaller y in bottom-up? anchors use line y)
        # Use vertical distance only.
        dist = abs(y - bbox[1])
        dist2 = abs(y - bbox[3])
        d = min(dist, dist2)
        if best is None or d < best[0]:
            best = (d, label)
    if best and best[0] < 80:
        return str(best[1])
    # fallback: any caption text on page
    for cap in caption_lines:
        m = _CAPTION_RE.search(str(cap.get("line") or cap.get("label") or ""))
        if m:
            return m.group(0)
    return None


def parse_figures_from_markdown(md: str) -> List[Dict[str, str]]:
    """Round-trip: ![alt](assets/figures/...) in order."""
    out: List[Dict[str, str]] = []
    for m in re.finditer(r"!\[([^\]]*)\]\((assets/figures/[^)]+)\)", md):
        out.append({"alt": m.group(1), "path": m.group(2)})
    return out


def parse_formulas_from_markdown(md: str) -> List[Dict[str, Any]]:
    """Round-trip: $$...$$ and <!-- formula_failed: ... --> (+ optional image)."""
    out: List[Dict[str, Any]] = []
    # failed blocks
    for m in re.finditer(
        r"<!--\s*formula_failed:\s*(.*?)\s*-->(?:\s*\n\s*!\[formula\]\((assets/formulas/[^)]+)\))?",
        md,
    ):
        out.append(
            {
                "kind": "failed",
                "reason": m.group(1),
                "path": m.group(2),
                "latex": None,
            }
        )
    for m in re.finditer(r"\$\$\s*([\s\S]*?)\s*\$\$", md):
        out.append({"kind": "latex", "latex": m.group(1).strip(), "path": None})
    return out
