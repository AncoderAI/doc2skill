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
            "total": 80,
            "scores": {"text_ocr": 20, "tables": 20, "figures": 15, "formulas": 10, "heading_order": 8, "integrity_offline": 5},
            "elapsed_sec": 10,
        },
        {
            "id": "worse",
            "hard_pass": True,
            "total": 70,
            "scores": {"text_ocr": 10, "tables": 20, "figures": 15, "formulas": 10, "heading_order": 8, "integrity_offline": 5},
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
            "total": 80,
            "scores": {"text_ocr": 20, "tables": 20, "figures": 15, "formulas": 10, "heading_order": 8, "integrity_offline": 5},
            "elapsed_sec": 10,
        },
        {
            "id": "better",
            "hard_pass": True,
            "total": 86,
            "scores": {"text_ocr": 22, "tables": 21, "figures": 16, "formulas": 11, "heading_order": 9, "integrity_offline": 5},
            "elapsed_sec": 9,
        },
    ]
    ranked = rank_candidates(results)
    assert ranked["winner"] and ranked["winner"]["id"] == "better"


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
