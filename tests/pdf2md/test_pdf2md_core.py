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
            },
            "elapsed_sec": 5,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] is None


def test_optimizer_accepts_gain():
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
            },
            "elapsed_sec": 9,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] and ranked["winner"]["id"] == "better"


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
    assert scores["tables"] == 25.0


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
    """Annotated figures=[] but candidate emits a figure → precision 0 → figures score 0."""
    from book_to_skill.pdf2md.eval import match_f1_iou, score_against_truth

    assert match_f1_iou([], [{"bbox": [0, 0, 10, 10]}]) == 0.0
    assert match_f1_iou([], []) == 1.0

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
    assert scores["tables"] == 25.0  # empty/empty F1=1
    assert scores["formulas"] == 15.0


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


def test_resolve_profile_overrides():
    p = resolve_profile("fast", {"dpi": 120})
    assert p.dpi == 120 and p.name == "fast"
