"""Unit tests for pdf2md IR, classify, tables, quality gates, optimizer rank."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_to_skill.pdf2md.classify import (
    classify_page,
    garbage_ratio,
    repeated_line_ratio,
    strip_watermarks,
)
from book_to_skill.pdf2md.convert import convert_pdf
from book_to_skill.pdf2md.eval import cer, kendall_tau, validate_bundle, wer
from book_to_skill.pdf2md.figures import formula_failure, formula_from_latex, normalize_latex
from book_to_skill.pdf2md.ir import SCHEMA_VERSION, validate_ir_dict
from book_to_skill.pdf2md.optimize.search import generate_candidates, rank_candidates
from book_to_skill.pdf2md.profiles import ConvertProfile, resolve_profile
from book_to_skill.pdf2md.tables import (
    grid_to_table,
    parse_html_table,
    parse_markdown_table,
    table_to_html,
    table_to_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def test_ir_schema_version():
    assert SCHEMA_VERSION == "1.0.0"
    errs = validate_ir_dict(
        {
            "schema_version": SCHEMA_VERSION,
            "source_sha256": "abc",
            "page_count": 1,
            "pages": [],
            "blocks": [
                {"block_id": "p0001-text-0000", "type": "text", "page": 1, "text": "hi"}
            ],
        }
    )
    assert errs == []


def test_classify_broken_encoding():
    junk = ("\x00\x00ab" * 50)  # ~50% NULs
    prof = ConvertProfile()
    assert garbage_ratio(junk) >= prof.force_ocr_garbage_ratio
    ptype, force = classify_page(junk, embedded_image_count=1, profile=prof)
    assert force is True
    assert ptype.value == "broken-encoding"


def test_classify_image_based_empty():
    prof = ConvertProfile()
    ptype, force = classify_page("", embedded_image_count=2, profile=prof)
    assert force and ptype.value == "image-based"


def test_watermark_strip_and_ratio():
    pages = ["WM\nhello\nWM", "WM\nworld\nWM", "WM\nfoo\nWM"]
    from book_to_skill.pdf2md.classify import repeated_line_candidates

    cands = repeated_line_candidates(pages, fraction=0.5)
    assert any(t == "WM" for t, _ in cands)
    cleaned = strip_watermarks(pages[0], ["WM"])
    assert "WM" not in cleaned
    assert repeated_line_ratio("a\na\na\nb") == 0.75


def test_garbage_ratio():
    assert garbage_ratio("hello") == 0.0
    assert garbage_ratio("\x00\x00ab") >= 0.5


def test_table_markdown_roundtrip():
    table = grid_to_table([["A", "B"], ["1", "2"]])
    md = table_to_markdown(table)
    back = parse_markdown_table(md)
    assert back is not None
    assert back.rows == 2 and back.cols == 2
    assert back.cells[0].text == "A"


def test_table_html_spans_roundtrip():
    table = grid_to_table([["H1", "H2"], ["x", "y"]])
    table.cells[0].colspan = 1
    table.has_spans = True
    table.cells[2].rowspan = 1
    html = table_to_html(table)
    assert "<table>" in html
    back = parse_html_table(html)
    assert back is not None
    assert back.rows == 2


def test_formula_normalize_and_failure():
    latex, tokens = normalize_latex(r"$\lambda = \lambda_0$")
    assert "lambda" in latex or "\\lambda" in latex
    assert tokens
    ok = formula_from_latex(r"\frac{a}{b}", confidence=0.9)
    assert not ok.failed and ok.latex
    bad = formula_failure(None, "no model")
    assert bad.failed and bad.latex is None


def test_cer_wer_kendall():
    assert cer("abc", "abc") == 0.0
    assert wer("a b c", "a b c") == 0.0
    assert kendall_tau(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    assert kendall_tau(["a", "b", "c"], ["c", "b", "a"]) == -1.0


def test_optimizer_rejects_regression():
    cov = {
        "scored_dimensions": ["text_ocr", "tables", "figures", "formulas", "heading_order"],
        "truth_coverage": {"pages_annotated": 12},
    }
    results = [
        {
            "id": "incumbent",
            "hard_pass": True,
            "total_normalized_100": 80,
            "max_possible": 100,
            "scores": {
                "text_ocr": 20,
                "tables": 20,
                "figures": 15,
                "formulas": 10,
                "heading_order": 8,
                "integrity_offline": 5,
                "max_possible": 100,
                "total_normalized_100": 80,
                **cov,
            },
            "elapsed_sec": 10,
        },
        {
            "id": "worse",
            "hard_pass": True,
            "total_normalized_100": 70,
            "max_possible": 100,
            "scores": {
                "text_ocr": 10,
                "tables": 20,
                "figures": 15,
                "formulas": 10,
                "heading_order": 8,
                "integrity_offline": 5,
                "max_possible": 100,
                "total_normalized_100": 70,
                **cov,
            },
            "elapsed_sec": 5,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] is None


def test_optimizer_accepts_gain():
    cov = {
        "scored_dimensions": ["text_ocr", "tables", "figures", "formulas", "heading_order"],
        "truth_coverage": {"pages_annotated": 12},
    }
    results = [
        {
            "id": "incumbent",
            "hard_pass": True,
            "total_normalized_100": 80,
            "max_possible": 100,
            "scores": {
                "text_ocr": 20,
                "tables": 20,
                "figures": 15,
                "formulas": 10,
                "heading_order": 8,
                "integrity_offline": 5,
                "max_possible": 100,
                "total_normalized_100": 80,
                **cov,
            },
            "elapsed_sec": 10,
        },
        {
            "id": "better",
            "hard_pass": True,
            "total_normalized_100": 86,
            "max_possible": 100,
            "scores": {
                "text_ocr": 22,
                "tables": 21,
                "figures": 16,
                "formulas": 11,
                "heading_order": 9,
                "integrity_offline": 5,
                "max_possible": 100,
                "total_normalized_100": 86,
                **cov,
            },
            "elapsed_sec": 9,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] and ranked["winner"]["id"] == "better"


def test_optimizer_rejects_insufficient_truth_coverage():
    results = [
        {
            "id": "incumbent",
            "hard_pass": True,
            "total_normalized_100": 50,
            "max_possible": 45,
            "scores": {
                "tables": 0.0,
                "figures": 0.0,
                "scored_dimensions": ["tables", "figures"],
                "truth_coverage": {"pages_annotated": 6},
                "max_possible": 45,
                "total_normalized_100": 50,
            },
            "elapsed_sec": 1,
        },
        {
            "id": "cand",
            "hard_pass": True,
            "total_normalized_100": 80,
            "max_possible": 45,
            "scores": {
                "tables": 12.5,
                "figures": 0.0,
                "scored_dimensions": ["tables", "figures"],
                "truth_coverage": {"pages_annotated": 6},
                "max_possible": 45,
                "total_normalized_100": 80,
            },
            "elapsed_sec": 1,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] is None
    assert ranked["reason"] == "insufficient_truth_coverage"


def test_optimizer_rejects_zero_max_possible():
    results = [
        {
            "id": "incumbent",
            "hard_pass": True,
            "total_normalized_100": 0,
            "max_possible": 0,
            "scores": {"max_possible": 0, "total_normalized_100": 0},
            "elapsed_sec": 10,
        },
        {
            "id": "cand",
            "hard_pass": True,
            "total_normalized_100": 0,
            "max_possible": 0,
            "scores": {"max_possible": 0, "total_normalized_100": 0},
            "elapsed_sec": 5,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] is None
    assert ranked["reason"] == "no_comparable_truth"


def test_score_against_truth_fail_closed_empty(tmp_path):
    from book_to_skill.pdf2md.eval import score_against_truth

    (tmp_path / "document.md").write_text("<!-- page: 1 -->\nhello\n", encoding="utf-8")
    (tmp_path / "document.ir.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source_sha256": "x",
                "page_count": 1,
                "pages": [],
                "blocks": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "quality-report.json").write_text("{}", encoding="utf-8")
    scores = score_against_truth(tmp_path, {})
    assert scores["max_possible"] == 0
    assert scores["total_raw"] == 0.0
    assert scores["total_normalized_100"] == 0.0
    assert set(scores["unscored_dimensions"]) == {
        "text_ocr",
        "heading_order",
        "tables",
        "figures",
        "formulas",
        "integrity_offline",
    }
    assert scores["scored_dimensions"] == []
    assert all(scores[d] is None for d in scores["unscored_dimensions"])


def test_score_against_truth_page_aligned(tmp_path):
    from book_to_skill.pdf2md.eval import score_against_truth

    md = "<!-- page: 1 -->\nalpha\n\n<!-- page: 2 -->\nbeta gamma\n"
    (tmp_path / "document.md").write_text(md, encoding="utf-8")
    (tmp_path / "document.ir.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source_sha256": "x",
                "page_count": 2,
                "pages": [],
                "blocks": [
                    {"block_id": "p1-t", "type": "text", "page": 1, "text": "alpha"},
                    {"block_id": "p2-t", "type": "text", "page": 2, "text": "beta gamma"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "quality-report.json").write_text("{}", encoding="utf-8")
    truth = {
        "pages_total": 96,
        "pages": {
            "1": {
                "page": 1,
                "text": "alpha",
                "blocks": [{"type": "text", "order": 0, "bbox": [0, 0, 10, 10]}],
                "tables": [],
                "figures": [],
                "formulas": [],
                "provenance": {
                    "text": {"level": "silver", "method": "test", "agreement": 1.0},
                    "tables": {"level": "silver", "method": "test", "agreement": 1.0},
                    "figures": {"level": "silver", "method": "test", "agreement": 1.0},
                    "formulas": {"level": "silver", "method": "test", "agreement": 1.0},
                },
            }
        },
    }
    scores = score_against_truth(tmp_path, truth)
    assert scores["truth_coverage"]["pages_annotated"] == 1
    assert scores["truth_coverage"]["pages_total"] == 96
    assert scores["text_ocr"] == 25.0
    assert scores["heading_order"] == 10.0
    assert "integrity_offline" in scores["unscored_dimensions"]
    assert scores["max_possible"] > 0
    assert scores["total_normalized_100"] == round(
        100.0 * scores["total_raw"] / scores["max_possible"], 2
    )


def test_score_against_truth_disputed_excluded(tmp_path):
    """disputed fields are unscored (same as null for denominator)."""
    from book_to_skill.pdf2md.eval import score_against_truth

    (tmp_path / "document.md").write_text("<!-- page: 1 -->\nalpha\n", encoding="utf-8")
    (tmp_path / "document.ir.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source_sha256": "x",
                "page_count": 1,
                "pages": [],
                "blocks": [{"block_id": "p1", "type": "text", "page": 1, "text": "alpha"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "quality-report.json").write_text("{}", encoding="utf-8")
    truth = {
        "pages_total": 10,
        "pages": {
            "1": {
                "page": 1,
                "text": "alpha",
                "blocks": [{"type": "text", "order": 0, "bbox": None}],
                "tables": [],
                "figures": [],
                "formulas": [],
                "provenance": {
                    "text": {
                        "level": "disputed",
                        "method": "t",
                        "agreement": 0.5,
                        "cer": 0.5,
                    },
                    "tables": {"level": "silver", "method": "t", "agreement": 1.0},
                    "figures": {"level": "silver", "method": "t", "agreement": 1.0},
                    "formulas": {"level": "silver", "method": "t", "agreement": 1.0},
                },
            }
        },
    }
    scores = score_against_truth(tmp_path, truth)
    assert scores["text_ocr"] is None
    assert "text_ocr" in scores["unscored_dimensions"]
    assert scores["truth_coverage"]["disputed_fields"] == 1
    assert scores["truth_coverage"]["scorable_by_field"]["text"] == 0
    assert scores["tables"] is None
    assert "tables" in scores["unscored_dimensions"]


def test_agreement_clamp_nonnegative():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("runs/p4/scripts").resolve()))
    from p4_lib import agreement_from_cer, level_from_agreement

    agr, c = agreement_from_cer(1.45)
    assert c == 1.45
    assert agr == 0.0
    assert level_from_agreement(agr) == "disputed"
    agr2, _ = agreement_from_cer(0.02)
    assert agr2 == 0.98
    assert level_from_agreement(agr2) == "silver"


def test_f1_false_positive_on_blank_annotation(tmp_path):
    """Annotated figures=[] but candidate emits a figure → precision 0 → figures score 0.

    Empty ∩ empty for tables/formulas → unscored (null), not a free full mark.
    """
    from book_to_skill.pdf2md.eval import match_f1_iou, score_against_truth

    assert match_f1_iou([], [{"bbox": [0, 0, 10, 10]}]) == 0.0
    assert match_f1_iou([], []) is None

    (tmp_path / "document.md").write_text("<!-- page: 14 -->\n\n", encoding="utf-8")
    (tmp_path / "document.ir.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source_sha256": "x",
                "page_count": 1,
                "pages": [],
                "blocks": [
                    {
                        "block_id": "p14-fig",
                        "type": "figure",
                        "page": 14,
                        "bbox": [0, 0, 100, 100],
                        "figure": {"asset_path": "assets/figures/page-0014-full.png"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "quality-report.json").write_text("{}", encoding="utf-8")
    truth = {
        "pages_total": 154,
        "pages": {
            "14": {
                "page": 14,
                "text": "",
                "blocks": [],
                "tables": [],
                "figures": [],
                "formulas": [],
                "provenance": {
                    "text": {"level": "silver", "method": "blank", "agreement": 1.0},
                    "tables": {"level": "silver", "method": "blank", "agreement": 1.0},
                    "figures": {"level": "silver", "method": "blank", "agreement": 1.0},
                    "formulas": {"level": "silver", "method": "blank", "agreement": 1.0},
                },
            }
        },
    }
    scores = score_against_truth(tmp_path, truth)
    assert scores["figures"] == 0.0
    assert scores["heading_order"] == 0.0  # blocks=[] but hyp has a figure block
    assert scores["text_ocr"] == 25.0  # empty/empty CER
    assert scores["tables"] is None  # empty ∩ empty → unscored
    assert scores["formulas"] is None
    assert "tables" in scores["unscored_dimensions"]
    assert "formulas" in scores["unscored_dimensions"]


def test_empty_empty_dimension_unscored(tmp_path):
    """ref=[] and hyp=[] must not grant full marks (frozen-constant trap)."""
    from book_to_skill.pdf2md.eval import score_against_truth

    (tmp_path / "document.md").write_text("<!-- page: 1 -->\nalpha\n", encoding="utf-8")
    (tmp_path / "document.ir.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source_sha256": "x",
                "page_count": 1,
                "pages": [],
                "blocks": [{"block_id": "p1", "type": "text", "page": 1, "text": "alpha"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "quality-report.json").write_text("{}", encoding="utf-8")
    truth = {
        "pages_total": 10,
        "pages": {
            "1": {
                "page": 1,
                "text": "alpha",
                "blocks": [{"type": "text", "order": 0, "bbox": [0, 0, 1, 1]}],
                "tables": [],
                "figures": [],
                "formulas": [],
                "provenance": {
                    "text": {"level": "silver", "method": "t", "agreement": 1.0},
                    "tables": {"level": "silver", "method": "t", "agreement": 1.0},
                    "figures": {"level": "silver", "method": "t", "agreement": 1.0},
                    "formulas": {"level": "silver", "method": "t", "agreement": 1.0},
                },
            }
        },
    }
    scores = score_against_truth(tmp_path, truth)
    assert scores["figures"] is None
    assert scores["formulas"] is None
    assert scores["tables"] is None
    assert scores["text_ocr"] == 25.0
    assert scores["max_possible"] == 35  # text 25 + heading 10 only
    assert "figures" in scores["unscored_dimensions"]
    assert "formulas" in scores["unscored_dimensions"]
    assert "tables" in scores["unscored_dimensions"]


def test_generate_candidates_budget():
    cands = generate_candidates(5)
    assert len(cands) == 5
    assert cands[0][0] == "incumbent"


@pytest.mark.parametrize(
    "name",
    ["native_text.pdf", "watermark_repeat.pdf", "formula_page.pdf", "mixed_pages.pdf"],
)
def test_convert_synthetic(name, tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    pdf = FIXTURES / name
    assert pdf.is_file()
    out = tmp_path / name.replace(".pdf", "")
    report = convert_pdf(pdf, out, profile="fast", strict=False)
    assert (out / "document.md").is_file()
    assert (out / "document.ir.json").is_file()
    assert (out / "quality-report.json").is_file()
    ir = json.loads((out / "document.ir.json").read_text(encoding="utf-8"))
    assert validate_ir_dict(ir) == []
    assert validate_bundle(out)["ok"]
    assert report["hard_gates"]["network_blocked"] is True


def test_watermark_not_silent_success(tmp_path):
    """Repeated watermark-heavy output must not pass hard gates."""
    pytest.importorskip("pypdfium2")
    pdf = FIXTURES / "watermark_repeat.pdf"
    out = tmp_path / "wm"
    report = convert_pdf(pdf, out, profile="fast", strict=True)
    # After watermark strip, content remains — but if somehow only watermark, fail.
    # Ensure quality report exists and net guard on.
    assert report["hard_gates"]["network_blocked"]
    md = (out / "document.md").read_text(encoding="utf-8")
    # Watermark line should be stripped from majority boilerplate
    assert md.count("WATERMARK-CONFIDENTIAL-ACME") <= 1


def test_corrupt_pdf_fails_loudly(tmp_path):
    pytest.importorskip("pypdf")
    pdf = FIXTURES / "corrupt.pdf"
    out = tmp_path / "corrupt"
    with pytest.raises(Exception):
        convert_pdf(pdf, out, profile="fast", strict=True)


def test_asset_path_safety(tmp_path):
    from book_to_skill.pdf2md.quality import _broken_asset_refs

    md = "![x](assets/figures/a.png)\n![bad](../../../etc/passwd)\n![http](https://evil.example/x.png)\n"
    (tmp_path / "assets" / "figures").mkdir(parents=True)
    (tmp_path / "assets" / "figures" / "a.png").write_bytes(b"PNG")
    broken = _broken_asset_refs(md, tmp_path)
    assert "assets/figures/a.png" not in broken
    assert any("etc/passwd" in b or "https://" in b for b in broken)


def test_formula_feature_threshold_conservative():
    from book_to_skill.pdf2md.figures import score_formula_line

    ok = score_formula_line("λ = λ0 · πT")
    assert ok.passed and "greek" in ok.classes_hit and "operators" in ok.classes_hit
    # IEC table-cell law without λ on same line
    cell = score_formula_line("=0.024×D (1)")
    assert cell.passed
    # footnote-like trailing (N) alone must not pass
    bad = score_formula_line("COB package note (4)")
    assert not bad.passed
    plain = score_formula_line("Introduction to reliability")
    assert not plain.passed
    # greek without operator (variable label / TOC) must not pass
    assert not score_formula_line("λ1").passed
    # OCR garbage with '=' but no greek / mul must not pass
    assert not score_formula_line("= Ws RAZA O<we<t; R20").passed


def test_figure_area_gates_full_page_and_tiny():
    from book_to_skill.pdf2md.figures import FigureCandidate, apply_area_gates

    full = FigureCandidate(bbox=(0, 0, 595.2, 841.3), route="raster", page=14)
    apply_area_gates(full, 595.2, 842.0)
    assert full.dropped == "full_page"
    tiny = FigureCandidate(bbox=(0, 0, 10, 10), route="raster", page=1)
    apply_area_gates(tiny, 595.0, 842.0)
    assert tiny.dropped == "too_small"


def test_formula_failed_partial_credit_in_truth_score(tmp_path):
    """Honest failure with crop+reason gets 0.3 weight; silent empty stays weaker."""
    from book_to_skill.pdf2md.eval import match_f1_iou_weighted, _formula_hyp_weight

    assert _formula_hyp_weight(
        {"failed": True, "asset_path": "assets/formulas/x.png", "failure_reason": "no_latex"}
    ) == 0.3
    assert _formula_hyp_weight({"failed": True, "asset_path": None, "failure_reason": "x"}) == 0.0
    assert _formula_hyp_weight({"failed": False, "latex": r"a=b"}) == 1.0

    refs = [{"bbox": [0, 0, 10, 10]}]
    hyps = [{"bbox": [0, 0, 10, 10]}]
    f1_ok = match_f1_iou_weighted(refs, hyps, [1.0])
    f1_partial = match_f1_iou_weighted(refs, hyps, [0.3])
    assert f1_ok > f1_partial > 0.0


def test_white_paper_ocr_condition_removed():
    """The old len(ocr_text)<80 full-page figure branch must not exist."""
    import inspect
    from book_to_skill.pdf2md import convert as convert_mod

    src = inspect.getsource(convert_mod._convert_local)
    assert "len(ocr_text.strip()) < 80" not in src
    assert "page-full.png" not in src or "full_page" in src


def test_cli_generated_corpus_follows_profile(tmp_path):
    from book_to_skill.pdf2md.cli import _write_generated_corpus

    pdf = FIXTURES / "native_text.pdf"
    path = _write_generated_corpus([str(pdf)], tmp_path, sample=0, profile="accurate")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["documents"][0]["profile"] == "accurate"


def test_ocr_table_grid_requires_scorable_shape():
    from book_to_skill.pdf2md.ir import TableBlock, TableCell
    from book_to_skill.pdf2md.tables import extract_tables_from_ocr_words, is_scorable_table

    shell = {"rows": 0, "cols": 0, "bbox": None, "caption": "Tabelle 1", "cells": None}
    assert not is_scorable_table(shell)
    stub = TableBlock(
        rows=1,
        cols=1,
        cells=[TableCell(text="Tabelle 1", row=0, col=0)],
        bbox=None,
    )
    assert not is_scorable_table(stub)

    # Synthetic 3x3 numeric grid of word boxes
    words = []
    for r in range(3):
        for c in range(3):
            x0, y0 = 10 + c * 40, 100 + r * 12
            words.append(((x0, y0, x0 + 20, y0 + 8), f"{r}.{c}"))
    tabs = extract_tables_from_ocr_words(words, page_w=400, page_h=400)
    assert len(tabs) >= 1
    assert is_scorable_table(tabs[0])
    assert tabs[0].rows >= 2 and tabs[0].cols >= 2
    assert tabs[0].bbox is not None


def test_match_f1_no_bbox_cannot_match():
    """ref or hyp missing bbox → never matched; still counts in P/R denominators."""
    from book_to_skill.pdf2md.eval import match_f1_iou

    refs = [{"bbox": None}, {"bbox": [0, 0, 10, 10]}]
    hyps = [{"bbox": None}, {"bbox": [0, 0, 10, 10]}]
    # Only the bbox pair can match → credit=1, P=1/2, R=1/2 → F1=0.5
    assert match_f1_iou(refs, hyps) == 0.5
    # Two no-bbox items alone: no match, F1=0 (not count-matched)
    assert match_f1_iou([{"bbox": None}], [{"bbox": None}]) == 0.0


def test_resolve_profile_overrides():
    p = resolve_profile("fast", {"dpi": 120})
    assert p.dpi == 120 and p.name == "fast"


def test_vector_figures_use_pymupdf_not_pdfplumber():
    import inspect
    from book_to_skill.pdf2md import figures as fig_mod

    src = inspect.getsource(fig_mod.detect_vector_figures)
    assert "get_drawings" in src
    assert "pdfplumber" not in src


def test_product_path_has_no_layout_model_imports():
    """Layout ONNX must stay on silver side (runs/p4/scripts), never product path."""
    root = Path(__file__).resolve().parents[2] / "book_to_skill" / "pdf2md"
    banned = ("silver_layout", "PP-DocLayout", "onnxruntime", "alex-dinh")
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{path.name}:{token}")
    assert offenders == []


def test_three_family_empty_vs_layout_formulas_disputed():
    """A=B=0 and C>0 (layout false formulas) must be disputed, not silver."""
    import sys

    scripts = Path(__file__).resolve().parents[2] / "runs" / "p4" / "scripts"
    sys.path.insert(0, str(scripts))
    from three_family import resolve_three_family

    r = resolve_three_family({"A": 0, "B": 0, "C": 99})
    assert r["level"] == "disputed"
    assert "empty_but_C_nonempty" in r["reason"]
    r2 = resolve_three_family({"A": 0, "B": 0, "C": 0})
    assert r2["level"] == "silver"
    r3 = resolve_three_family({"A": None, "B": None, "C": 2})
    assert r3["level"] == "weak_single_family"
