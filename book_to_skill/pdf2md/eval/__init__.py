"""Evaluation helpers: scoring overlays and validators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from ..ir import validate_ir_dict
from ..tables import parse_html_table, parse_markdown_table


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


def score_against_truth(bundle_dir: Path, truth: Dict[str, Any]) -> Dict[str, float]:
    """Score a bundle against a truth manifest fragment."""
    md = (bundle_dir / "document.md").read_text(encoding="utf-8") if (bundle_dir / "document.md").exists() else ""
    ir = json.loads((bundle_dir / "document.ir.json").read_text(encoding="utf-8")) if (bundle_dir / "document.ir.json").exists() else {}
    hyp_text = md
    ref_text = truth.get("text", "")
    text_score = 25.0 * max(0.0, 1.0 - cer(ref_text, hyp_text)) if ref_text else 12.5

    ref_blocks = truth.get("block_ids", [])
    hyp_blocks = [b.get("block_id") for b in ir.get("blocks", [])]
    order_score = 10.0 * max(0.0, (kendall_tau(ref_blocks, hyp_blocks) + 1) / 2) if ref_blocks else 5.0

    ref_tables = truth.get("tables", [])
    hyp_tables = [b for b in ir.get("blocks", []) if b.get("type") == "table"]
    table_recall = len(hyp_tables) / max(len(ref_tables), 1) if ref_tables else (1.0 if hyp_tables else 0.5)
    table_score = 25.0 * min(1.0, table_recall)

    ref_figs = truth.get("figures", 0)
    hyp_figs = sum(1 for b in ir.get("blocks", []) if b.get("type") == "figure")
    fig_score = 20.0 * min(1.0, hyp_figs / max(ref_figs, 1)) if ref_figs else (10.0 if hyp_figs else 8.0)

    ref_forms = truth.get("formulas", [])
    hyp_forms = [b for b in ir.get("blocks", []) if b.get("type") == "formula"]
    formula_score = 15.0 * min(1.0, len([f for f in hyp_forms if not (f.get("formula") or {}).get("failed")]) / max(len(ref_forms), 1)) if ref_forms else 8.0

    integrity = 5.0 if validate_bundle(bundle_dir)["ok"] else 1.0
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
