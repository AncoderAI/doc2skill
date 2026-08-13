"""Profile search space and ranking for pdf2md optimizer."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from ..profiles import ConvertProfile, PROFILES


# Finite declarative search dimensions (v1: profiles only).
SEARCH_DIMS: List[Dict[str, Any]] = [
    {"dpi": 150},
    {"dpi": 200},
    {"dpi": 300},
    {"ocr_psm": 3},
    {"ocr_psm": 6},
    {"table_mode": "fast"},
    {"table_mode": "accurate"},
    {"force_ocr_min_chars": 20},
    {"force_ocr_min_chars": 40},
    {"force_ocr_min_chars": 80},
    {"watermark_page_fraction": 0.4},
    {"watermark_page_fraction": 0.5},
    {"max_repeated_line_ratio": 0.15},
    {"max_repeated_line_ratio": 0.20},
    {"enable_formulas": True},
    {"enable_formulas": False},
    {"enable_ocr_tables": True},
    {"enable_ocr_tables": False},
    {"html_tables_on_span": True},
]


def generate_candidates(budget: int, base: str = "auto") -> List[Tuple[str, ConvertProfile]]:
    base_prof = deepcopy(PROFILES.get(base) or PROFILES["auto"])
    out: List[Tuple[str, ConvertProfile]] = [("incumbent", deepcopy(base_prof))]
    for i, dims in enumerate(SEARCH_DIMS):
        if len(out) >= budget:
            break
        prof = deepcopy(base_prof)
        for k, v in dims.items():
            setattr(prof, k, v)
        out.append((f"cand-{i:02d}-" + "-".join(f"{k}{v}" for k, v in dims.items()), prof))
    return out[:budget]


def _comparable_score(result: Dict[str, Any]) -> Optional[float]:
    """Return ranking key, or None if the result is not comparable."""
    max_possible = result.get("max_possible")
    if max_possible is None:
        max_possible = (result.get("scores") or {}).get("max_possible")
    if max_possible is None or int(max_possible) == 0:
        return None
    if "total_normalized_100" in result:
        return float(result["total_normalized_100"])
    scores = result.get("scores") or {}
    if "total_normalized_100" in scores:
        return float(scores["total_normalized_100"])
    return None


def _truth_coverage_ok(result: Dict[str, Any]) -> bool:
    """Require enough scored dims + annotated pages before a winner may be named."""
    scores = result.get("scores") if isinstance(result.get("scores"), dict) else result
    if not isinstance(scores, dict):
        return False
    scored = scores.get("scored_dimensions") or []
    cov = scores.get("truth_coverage") or {}
    pages_ann = int(cov.get("pages_annotated") or 0)
    return len(scored) >= 3 and pages_ann >= 10


def rank_candidates(
    results: List[Dict[str, Any]],
    *,
    min_total_gain: float = 2.0,
    max_dim_drop: float = 1.0,
    near_tie: float = 0.5,
    resource_improve: float = 0.20,
) -> Dict[str, Any]:
    """Select winner vs incumbent using total_normalized_100 only.

    Candidates with max_possible==0 are not comparable and cannot win.
    Scoring base with scored_dimensions < 3 or pages_annotated < 10 cannot
    name a winner (``insufficient_truth_coverage``).
    """
    by_id = {r["id"]: r for r in results}
    if "incumbent" not in by_id:
        return {"winner": None, "reason": "no_incumbent"}
    inc = by_id["incumbent"]
    inc_score = _comparable_score(inc)
    if inc_score is None:
        return {
            "winner": None,
            "reason": "no_comparable_truth",
            "top3": [],
        }
    if not _truth_coverage_ok(inc):
        return {
            "winner": None,
            "reason": "insufficient_truth_coverage",
            "top3": [],
        }

    survivors = []
    for r in results:
        if not r.get("hard_pass"):
            continue
        if not _truth_coverage_ok(r):
            continue
        s = _comparable_score(r)
        if s is None:
            continue
        survivors.append((s, r))
    survivors.sort(key=lambda pair: (-pair[0], pair[1].get("elapsed_sec", 1e9)))
    top3 = [r for _, r in survivors[:3]]

    winner = None
    reason = "no_improvement"
    for cand in top3:
        if cand["id"] == "incumbent":
            continue
        cand_score = _comparable_score(cand)
        if cand_score is None:
            continue
        gain = cand_score - inc_score
        dim_ok = True
        cand_scores = cand.get("scores") or {}
        inc_scores = inc.get("scores") or {}
        for dim in ("text_ocr", "heading_order", "tables", "figures", "formulas", "integrity_offline"):
            c_val = cand_scores.get(dim)
            i_val = inc_scores.get(dim)
            if c_val is None or i_val is None:
                continue
            if float(c_val) < float(i_val) - max_dim_drop:
                dim_ok = False
                break
        if not dim_ok:
            continue
        if gain >= min_total_gain:
            winner = cand
            reason = f"quality_gain:{gain:.2f}"
            break
        if abs(gain) <= near_tie:
            # resource improvement
            inc_t = inc.get("elapsed_sec") or 1.0
            cand_t = cand.get("elapsed_sec") or 1.0
            if cand_t <= inc_t * (1.0 - resource_improve):
                winner = cand
                reason = f"resource_gain_time:{inc_t - cand_t:.2f}"
                break
            inc_m = inc.get("peak_memory_mb")
            cand_m = cand.get("peak_memory_mb")
            if inc_m and cand_m and cand_m <= inc_m * (1.0 - resource_improve):
                winner = cand
                reason = "resource_gain_memory"
                break

    return {"winner": winner, "reason": reason, "top3": [c["id"] for c in top3]}
