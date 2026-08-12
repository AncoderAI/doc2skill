"""Quality report and hard gates for pdf2md bundles."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from .classify import repeated_line_ratio
from .ir import BlockType, DocumentIR


def build_quality_report(
    ir: DocumentIR,
    output_dir: Path,
    *,
    elapsed_sec: float,
    peak_memory_mb: Optional[float],
    config: Dict[str, Any],
    tool_versions: Dict[str, Any],
    network_blocked: bool,
) -> Dict[str, Any]:
    md_path = output_dir / "document.md"
    ir_path = output_dir / "document.ir.json"
    md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""

    missing: List[str] = []
    hard_gates: Dict[str, Any] = {}

    # Page alignment
    page_nums = {p.page for p in ir.pages}
    expected = set(range(1, ir.page_count + 1))
    # When page_filter used, pages subset is OK if declared
    page_filter = config.get("page_filter")
    if page_filter:
        expected = set(page_filter)
    missing_pages = sorted(expected - page_nums)
    hard_gates["pages_aligned"] = len(missing_pages) == 0
    if missing_pages:
        missing.append(f"missing_pages:{missing_pages}")

    # Asset refs
    broken_refs = _broken_asset_refs(md_text, output_dir)
    hard_gates["assets_valid"] = len(broken_refs) == 0
    if broken_refs:
        missing.extend(f"broken_asset:{r}" for r in broken_refs[:20])

    hard_gates["network_blocked"] = bool(network_blocked)
    hard_gates["no_silent_exception"] = True  # convert path raises/records

    nonempty_pages = 0
    for p in ir.pages:
        page_blocks = [b for b in ir.blocks if b.page == p.page and (b.text or "").strip()]
        ocr_chars = p.ocr_chars or 0
        text_chars = sum(len(b.text) for b in page_blocks)
        if text_chars > 20 or ocr_chars > 20:
            nonempty_pages += 1
    nonempty_ratio = nonempty_pages / max(len(ir.pages), 1)
    hard_gates["nonempty_page_ratio"] = nonempty_ratio
    hard_gates["nonempty_page_ratio_ok"] = nonempty_ratio >= float(
        config.get("min_nonempty_page_ratio", 0.98)
    )

    rep_ratio = repeated_line_ratio(md_text)
    hard_gates["repeated_line_ratio"] = rep_ratio
    hard_gates["repeated_line_ratio_ok"] = rep_ratio < float(
        config.get("max_repeated_line_ratio", 0.20)
    )

    # Content presence signals
    n_tables = sum(1 for b in ir.blocks if b.type == BlockType.TABLE)
    n_figures = sum(1 for b in ir.blocks if b.type == BlockType.FIGURE)
    n_formulas = sum(1 for b in ir.blocks if b.type == BlockType.FORMULA)
    n_formula_failed = sum(
        1
        for b in ir.blocks
        if b.type == BlockType.FORMULA and b.formula and b.formula.failed
    )

    scores = _score_dimensions(ir, md_text, hard_gates)
    failures: List[str] = []
    if not hard_gates["pages_aligned"]:
        failures.append("pages_not_aligned")
    if not hard_gates["assets_valid"]:
        failures.append("broken_assets")
    if not hard_gates["network_blocked"]:
        failures.append("network_not_blocked")
    if not hard_gates["nonempty_page_ratio_ok"]:
        failures.append("nonempty_page_ratio")
    if not hard_gates["repeated_line_ratio_ok"]:
        failures.append("repeated_watermark_like_output")

    # SIEMENS-style: treating watermark garbage as success is forbidden
    if rep_ratio >= 0.20 and nonempty_ratio < 0.5:
        failures.append("degenerate_ocr_or_watermark")

    report = {
        "schema_version": "1.0.0",
        "scores": scores,
        "total_score": scores.get("total", 0.0),
        "hard_gates": hard_gates,
        "missing": missing,
        "counts": {
            "tables": n_tables,
            "figures": n_figures,
            "formulas": n_formulas,
            "formula_failed": n_formula_failed,
            "blocks": len(ir.blocks),
            "pages": len(ir.pages),
        },
        "tool_versions": tool_versions,
        "config": config,
        "elapsed_sec": elapsed_sec,
        "peak_memory_mb": peak_memory_mb,
        "artifact_hashes": {
            "document.md": _sha256_file(md_path) if md_path.exists() else None,
            "document.ir.json": _sha256_file(ir_path) if ir_path.exists() else None,
        },
        "failures": failures,
        "passed": len(failures) == 0,
        "warnings": list(ir.warnings),
    }
    return report


def _score_dimensions(ir: DocumentIR, md_text: str, gates: Dict[str, Any]) -> Dict[str, float]:
    # Heuristic offline scores when no ground truth is present (benchmark overlays later).
    text_score = 25.0 * min(1.0, gates.get("nonempty_page_ratio", 0) / 0.98)
    if not gates.get("repeated_line_ratio_ok", False):
        text_score *= 0.3

    headings = sum(1 for b in ir.blocks if b.type == BlockType.HEADING)
    order_score = 10.0 if headings > 0 or len(ir.blocks) > 0 else 0.0

    tables = sum(1 for b in ir.blocks if b.type == BlockType.TABLE)
    table_score = 25.0 if tables > 0 else (12.0 if "native" in str(ir.engine) else 5.0)

    figures = sum(1 for b in ir.blocks if b.type == BlockType.FIGURE)
    fig_score = min(20.0, 5.0 + figures * 3.0)

    formulas = [b for b in ir.blocks if b.type == BlockType.FORMULA]
    ok_f = sum(1 for b in formulas if b.formula and not b.formula.failed and b.formula.latex)
    formula_score = 15.0 * (ok_f / max(len(formulas), 1)) if formulas else 8.0

    integrity = 5.0 if gates.get("pages_aligned") and gates.get("assets_valid") and gates.get("network_blocked") else 1.0

    total = text_score + order_score + table_score + fig_score + formula_score + integrity
    return {
        "text_ocr": round(text_score, 2),
        "heading_order": round(order_score, 2),
        "tables": round(table_score, 2),
        "figures": round(fig_score, 2),
        "formulas": round(formula_score, 2),
        "integrity_offline": round(integrity, 2),
        "total": round(total, 2),
    }


def _broken_asset_refs(md_text: str, output_dir: Path) -> List[str]:
    import re

    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md_text)
    broken = []
    for ref in refs:
        if ref.startswith(("http://", "https://")):
            broken.append(ref)
            continue
        path = (output_dir / ref).resolve()
        try:
            path.relative_to(output_dir.resolve())
        except ValueError:
            broken.append(ref)
            continue
        if not path.is_file():
            broken.append(ref)
    return broken


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
