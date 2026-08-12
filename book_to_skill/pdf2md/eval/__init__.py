"""Evaluation helpers: scoring overlays and validators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ir import validate_ir_dict
from ..tables import parse_html_table, parse_markdown_table

DIM_MAX: Dict[str, float] = {
    "text_ocr": 25.0,
    "heading_order": 10.0,
    "tables": 25.0,
    "figures": 20.0,
    "formulas": 15.0,
    "integrity_offline": 5.0,
}

_PAGE_MARK_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->", re.IGNORECASE)
_PROVENANCE_FIELDS = ("text", "tables", "figures", "formulas")


def cer(ref: str, hyp: str) -> float:
    """Character error rate (Levenshtein / len(ref))."""
    if not ref:
        return 0.0 if not hyp else 1.0
    return _lev(ref, hyp) / len(ref)


def wer(ref: str, hyp: str) -> float:
    r = ref.split()
    h = hyp.split()
    if not r:
        return 0.0 if not h else 1.0
    return _lev_seq(r, h) / len(r)


def kendall_tau(order_a: List[str], order_b: List[str]) -> float:
    """Kendall tau in [-1,1] on intersection of IDs."""
    common = [x for x in order_a if x in set(order_b)]
    if len(common) < 2:
        return 1.0
    pos = {x: i for i, x in enumerate(order_b)}
    seq = [pos[x] for x in common]
    concord = 0
    discord = 0
    n = len(seq)
    for i in range(n):
        for j in range(i + 1, n):
            if seq[i] < seq[j]:
                concord += 1
            elif seq[i] > seq[j]:
                discord += 1
    total = concord + discord
    return (concord - discord) / total if total else 1.0


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def split_markdown_pages(md: str) -> Dict[int, str]:
    """Split document.md on <!-- page: N --> markers into {page_num: body}."""
    matches = list(_PAGE_MARK_RE.finditer(md))
    if not matches:
        return {}
    out: Dict[int, str] = {}
    for i, m in enumerate(matches):
        page = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        out[page] = md[start:end].strip()
    return out


def bbox_iou(
    a: Sequence[float], b: Sequence[float]
) -> float:
    if len(a) < 4 or len(b) < 4:
        return 0.0
    ax0, ay0, ax1, ay1 = map(float, a[:4])
    bx0, by0, bx1, by1 = map(float, b[:4])
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match_f1_iou(
    refs: Sequence[Dict[str, Any]],
    hyps: Sequence[Dict[str, Any]],
    *,
    iou_thresh: float = 0.5,
) -> float:
    """Greedy one-to-one F1 with bbox IoU ≥ thresh.

    Precision side is first-class: annotated-empty + candidate non-empty
    (false positives, e.g. white-paper full-page figures) → F1 = 0.0.
    Empty/empty → 1.0.
    """
    if not refs and not hyps:
        return 1.0
    if not refs and hyps:
        # False positives against an explicitly empty silver/gold annotation.
        return 0.0
    if refs and not hyps:
        # False negatives only → precision undefined/1 but recall 0 → F1 0
        return 0.0
    pairs: List[Tuple[float, int, int]] = []
    for i, ref in enumerate(refs):
        rb = ref.get("bbox")
        if not rb:
            continue
        for j, hyp in enumerate(hyps):
            hb = hyp.get("bbox")
            if not hb:
                continue
            iou = bbox_iou(rb, hb)
            if iou >= iou_thresh:
                pairs.append((iou, i, j))
    pairs.sort(reverse=True)
    used_r: set[int] = set()
    used_h: set[int] = set()
    matched = 0
    for _, i, j in pairs:
        if i in used_r or j in used_h:
            continue
        used_r.add(i)
        used_h.add(j)
        matched += 1
    # Refs/hyps without bbox: fall back to count residual (unmatched)
    refs_no_bbox = sum(1 for r in refs if not r.get("bbox"))
    hyps_no_bbox = sum(1 for h in hyps if not h.get("bbox"))
    # Count-only matching for bbox-less items
    count_match = min(refs_no_bbox, hyps_no_bbox)
    matched += count_match
    precision = matched / len(hyps)
    recall = matched / len(refs)
    return f1(precision, recall)


def validate_bundle(bundle_dir: Path) -> Dict[str, Any]:
    errors: List[str] = []
    md = bundle_dir / "document.md"
    irp = bundle_dir / "document.ir.json"
    qr = bundle_dir / "quality-report.json"
    for p in (md, irp, qr):
        if not p.is_file():
            errors.append(f"missing:{p.name}")
    ir = None
    if irp.is_file():
        ir = json.loads(irp.read_text(encoding="utf-8"))
        errors.extend(validate_ir_dict(ir))
    if md.is_file():
        text = md.read_text(encoding="utf-8")
        if "<!-- page:" not in text and ir and ir.get("page_count", 0) > 0:
            errors.append("missing_page_markers")
        # table round-trip
        for m in re.finditer(r"(?s)(<table>.*?</table>)", text):
            parsed = parse_html_table(m.group(1))
            if parsed is None:
                errors.append("html_table_parse_failed")
        for block in re.finditer(r"(?m)^(\|.+\|[ \t]*\n\|[-:| ]+\|[ \t]*\n(?:\|.+\|[ \t]*\n)+)", text):
            parsed = parse_markdown_table(block.group(1))
            if parsed is None:
                errors.append("md_table_parse_failed")
    return {"ok": len(errors) == 0, "errors": errors}


def _empty_score_result(
    *,
    pages_annotated: int = 0,
    pages_total: int = 0,
    gold_fields: int = 0,
    silver_fields: int = 0,
    disputed_fields: int = 0,
    scorable_by_field: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    dims = list(DIM_MAX.keys())
    out: Dict[str, Any] = {d: None for d in dims}
    out.update(
        {
            "scored_dimensions": [],
            "unscored_dimensions": dims,
            "max_possible": 0,
            "total_raw": 0.0,
            "total_normalized_100": 0.0,
            "total": 0.0,
            "truth_coverage": {
                "pages_annotated": pages_annotated,
                "pages_total": pages_total,
                "gold_fields": gold_fields,
                "silver_fields": silver_fields,
                "disputed_fields": disputed_fields,
                "scorable_by_field": scorable_by_field
                or {f: 0 for f in _PROVENANCE_FIELDS},
            },
        }
    )
    return out


def _page_truth_map(truth: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    raw = truth.get("pages") or {}
    out: Dict[int, Dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        page = int(v.get("page", k))
        out[page] = v
    return out


def _field_level(page: Dict[str, Any], field: str) -> Optional[str]:
    """Return gold/silver only. disputed is unscored (same as missing)."""
    prov = (page.get("provenance") or {}).get(field) or {}
    level = prov.get("level")
    if level in ("gold", "silver"):
        return level
    return None


def _coverage_counts(
    pages: Dict[int, Dict[str, Any]],
) -> Tuple[int, int, int, Dict[str, int]]:
    gold = silver = disputed = 0
    scorable_by_field = {f: 0 for f in _PROVENANCE_FIELDS}
    for page in pages.values():
        prov = page.get("provenance") or {}
        for field in _PROVENANCE_FIELDS:
            level = (prov.get(field) or {}).get("level")
            if level == "gold":
                gold += 1
                scorable_by_field[field] += 1
            elif level == "silver":
                silver += 1
                scorable_by_field[field] += 1
            elif level == "disputed":
                disputed += 1
    return gold, silver, disputed, scorable_by_field


def _dim_is_annotated(pages: Dict[int, Dict[str, Any]], dim: str) -> bool:
    if dim == "integrity_offline":
        # No gold/silver provenance path in A.3 schema → never scored from truth.
        return False
    if dim == "heading_order":
        # Only pages with blocks AND scorable text (gold/silver) contribute.
        return any(
            "blocks" in p and _field_level(p, "text") is not None for p in pages.values()
        )
    field = {
        "text_ocr": "text",
        "tables": "tables",
        "figures": "figures",
        "formulas": "formulas",
    }.get(dim)
    if not field:
        return False
    return any(_field_level(p, field) is not None for p in pages.values())


def _hyp_page_blocks(ir: Dict[str, Any], page: int) -> List[Dict[str, Any]]:
    return [b for b in ir.get("blocks", []) if int(b.get("page") or 0) == page]


def _heading_order_page_score(
    truth_page: Dict[str, Any], hyp_blocks: List[Dict[str, Any]]
) -> Optional[float]:
    if "blocks" not in truth_page:
        return None
    ref_blocks = sorted(
        truth_page.get("blocks") or [],
        key=lambda b: int(b.get("order") if b.get("order") is not None else 0),
    )
    if len(ref_blocks) < 1:
        # Explicitly empty annotation: any hypothesized blocks are false structure.
        return 10.0 if len(hyp_blocks) < 1 else 0.0
    if len(hyp_blocks) < 1:
        return 0.0
    # Build shared IDs by greedy type matching in reading order.
    ref_ids = [f"r{i}:{b.get('type')}" for i, b in enumerate(ref_blocks)]
    hyp_ids: List[str] = []
    used: set[int] = set()
    for hb in hyp_blocks:
        htype = hb.get("type")
        best = None
        for i, rb in enumerate(ref_blocks):
            if i in used:
                continue
            if rb.get("type") != htype:
                continue
            # Prefer bbox IoU when both have bbox
            score = 1.0
            if rb.get("bbox") and hb.get("bbox"):
                score = bbox_iou(rb["bbox"], hb["bbox"])
            if best is None or score > best[0]:
                best = (score, i)
        if best is not None and (best[0] >= 0.1 or ref_blocks[best[1]].get("type") == htype):
            used.add(best[1])
            hyp_ids.append(ref_ids[best[1]])
        else:
            hyp_ids.append(f"h-unmatched:{htype}:{len(hyp_ids)}")
    tau = kendall_tau(ref_ids, hyp_ids)
    return 10.0 * max(0.0, (tau + 1.0) / 2.0)


def _as_bbox_items(
    items: Sequence[Any], *, bbox_key: str = "bbox"
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict):
            out.append({"bbox": it.get(bbox_key) or it.get("bbox")})
        else:
            out.append({"bbox": None})
    return out


def score_against_truth(bundle_dir: Path, truth: Dict[str, Any]) -> Dict[str, Any]:
    """Page-aligned fail-closed scoring against gold/silver annotations.

    Only pages present in ``truth["pages"]`` participate. Dimensions without
    gold/silver provenance are ``null`` and excluded from the denominator.
    """
    truth = truth or {}
    pages_truth = _page_truth_map(truth)
    pages_total = int(truth.get("pages_total") or 0)
    gold_fields, silver_fields, disputed_fields, scorable_by_field = _coverage_counts(
        pages_truth
    )

    if not pages_truth:
        return _empty_score_result(
            pages_annotated=0,
            pages_total=pages_total,
            gold_fields=gold_fields,
            silver_fields=silver_fields,
            disputed_fields=disputed_fields,
            scorable_by_field=scorable_by_field,
        )

    md_path = bundle_dir / "document.md"
    ir_path = bundle_dir / "document.ir.json"
    md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    ir = json.loads(ir_path.read_text(encoding="utf-8")) if ir_path.is_file() else {}
    md_pages = split_markdown_pages(md)

    # --- text_ocr: char-weighted mean of per-page (1-CER)*25 ---
    text_score: Optional[float] = None
    if _dim_is_annotated(pages_truth, "text_ocr"):
        weighted_sum = 0.0
        weight = 0
        for page_no, tp in pages_truth.items():
            if _field_level(tp, "text") is None:
                continue
            ref = tp.get("text") or ""
            hyp = md_pages.get(page_no, "")
            w = max(len(ref), 1)
            page_s = 25.0 * max(0.0, 1.0 - cer(ref, hyp))
            weighted_sum += page_s * w
            weight += w
        text_score = round(weighted_sum / weight, 2) if weight else 0.0

    # --- heading_order: mean over pages that annotate blocks ---
    order_score: Optional[float] = None
    if _dim_is_annotated(pages_truth, "heading_order"):
        vals: List[float] = []
        weights: List[int] = []
        for page_no, tp in pages_truth.items():
            if "blocks" not in tp:
                continue
            # Disputed text pages do not contribute to heading_order.
            if _field_level(tp, "text") is None:
                continue
            hyp_blocks = _hyp_page_blocks(ir, page_no)
            s = _heading_order_page_score(tp, hyp_blocks)
            if s is None:
                continue
            n_ref = max(len(tp.get("blocks") or []), 1)
            vals.append(s)
            weights.append(n_ref)
        if vals:
            order_score = round(
                sum(v * w for v, w in zip(vals, weights)) / sum(weights), 2
            )
        else:
            order_score = None

    # --- tables / figures / formulas: pooled F1 on annotated pages ---
    def _pooled_f1(
        field: str, hyp_type: str, dim: str
    ) -> Optional[float]:
        if not _dim_is_annotated(pages_truth, dim):
            return None
        refs: List[Dict[str, Any]] = []
        hyps: List[Dict[str, Any]] = []
        for page_no, tp in pages_truth.items():
            if _field_level(tp, field) is None:
                continue
            refs.extend(_as_bbox_items(tp.get(field) or []))
            page_hyps = [
                b
                for b in _hyp_page_blocks(ir, page_no)
                if b.get("type") == hyp_type
            ]
            for b in page_hyps:
                bbox = b.get("bbox")
                if not bbox and hyp_type == "figure" and isinstance(b.get("figure"), dict):
                    bbox = b["figure"].get("bbox")
                if not bbox and hyp_type == "table" and isinstance(b.get("table"), dict):
                    bbox = b["table"].get("bbox")
                if not bbox and hyp_type == "formula" and isinstance(b.get("formula"), dict):
                    bbox = b["formula"].get("bbox")
                hyps.append({"bbox": bbox})
        return match_f1_iou(refs, hyps)

    table_f1 = _pooled_f1("tables", "table", "tables")
    fig_f1 = _pooled_f1("figures", "figure", "figures")
    formula_f1 = _pooled_f1("formulas", "formula", "formulas")

    table_score = round(25.0 * table_f1, 2) if table_f1 is not None else None
    fig_score = round(20.0 * fig_f1, 2) if fig_f1 is not None else None
    formula_score = round(15.0 * formula_f1, 2) if formula_f1 is not None else None

    # integrity: no provenance → always unscored under fail-closed
    integrity: Optional[float] = None

    dim_values: Dict[str, Optional[float]] = {
        "text_ocr": text_score,
        "heading_order": order_score,
        "tables": table_score,
        "figures": fig_score,
        "formulas": formula_score,
        "integrity_offline": integrity,
    }

    scored = [d for d, v in dim_values.items() if v is not None]
    unscored = [d for d, v in dim_values.items() if v is None]
    total_raw = round(sum(float(dim_values[d]) for d in scored), 2)
    max_possible = int(sum(DIM_MAX[d] for d in scored))
    if max_possible == 0:
        total_normalized = 0.0
    else:
        total_normalized = round(100.0 * total_raw / max_possible, 2)

    result: Dict[str, Any] = {d: dim_values[d] for d in DIM_MAX}
    result.update(
        {
            "scored_dimensions": scored,
            "unscored_dimensions": unscored,
            "max_possible": max_possible,
            "total_raw": total_raw,
            "total_normalized_100": total_normalized,
            # Alias for older callers; ranking must prefer total_normalized_100.
            "total": total_normalized,
            "truth_coverage": {
                "pages_annotated": len(pages_truth),
                "pages_total": pages_total or len(pages_truth),
                "gold_fields": gold_fields,
                "silver_fields": silver_fields,
                "disputed_fields": disputed_fields,
                "scorable_by_field": scorable_by_field,
            },
        }
    )
    return result


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins, delete, sub = cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _lev_seq(a: List[str], b: List[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins, delete, sub = cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]
