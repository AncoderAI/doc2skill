"""Profile search space and ranking for pdf2md optimizer."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

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


def rank_candidates(
    results: List[Dict[str, Any]],
    *,
    min_total_gain: float = 2.0,
    max_dim_drop: float = 1.0,
    near_tie: float = 0.5,
    resource_improve: float = 0.20,
) -> Dict[str, Any]:
    """Select winner vs incumbent. Known regressions must be rejected."""
    by_id = {r["id"]: r for r in results}
    if "incumbent" not in by_id:
        return {"winner": None, "reason": "no_incumbent"}
    inc = by_id["incumbent"]
    if not inc.get("hard_pass"):
        # still allow better candidates
        pass

    survivors = [r for r in results if r.get("hard_pass")]
    survivors.sort(key=lambda r: (-r.get("total", 0), r.get("elapsed_sec", 1e9)))
    top3 = survivors[:3]

    winner = None
    reason = "no_improvement"
    for cand in top3:
        if cand["id"] == "incumbent":
            continue
        gain = cand.get("total", 0) - inc.get("total", 0)
        dim_ok = True
        for dim in ("text_ocr", "heading_order", "tables", "figures", "formulas", "integrity_offline"):
            if cand.get("scores", {}).get(dim, 0) < inc.get("scores", {}).get(dim, 0) - max_dim_drop:
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
