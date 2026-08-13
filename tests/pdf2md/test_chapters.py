"""P7: chapter detection + physical PDF split."""

from __future__ import annotations

from book_to_skill.pdf2md.chapters import detect_chapters
from book_to_skill.pdf2md.split import split_by_chapters

from tests.pdf2md.fixtures.generate_synthetic import (
    generate_bad_toc_outline,
    generate_chapters_ok,
    generate_header_repeat,
    generate_no_text_layer,
    generate_number_gap,
    generate_number_regression,
    generate_split_heading,
    generate_split_heading_midpage,
    generate_split_toc_page,
    generate_toc_page,
)


def test_header_dedup_same_y_is_not_a_chapter(tmp_path):
    pdf = generate_header_repeat(tmp_path / "header_repeat.pdf")
    result = detect_chapters(pdf)
    assert result["source"] == "none"
    assert result["chapters"] == []
    assert result["warnings"]


def test_toc_page_with_three_titles_is_not_a_boundary(tmp_path):
    pdf = generate_toc_page(tmp_path / "toc_page.pdf")
    result = detect_chapters(pdf)
    real = [c for c in result["chapters"] if c["index"] >= 1]
    assert real, result
    assert all(c["start_page"] != 1 for c in real)
    assert result["source"] == "heading"


def test_bad_embedded_toc_falls_back_to_heading(tmp_path):
    pdf = generate_bad_toc_outline(tmp_path / "bad_toc_outline.pdf")
    result = detect_chapters(pdf)
    assert result["source"] == "heading"
    assert result["warnings"]
    real = [c for c in result["chapters"] if c["index"] >= 1]
    assert len(real) >= 2
    assert not any("1.5.1" in c["title"] for c in real)


def test_no_text_layer_is_none(tmp_path):
    pdf = generate_no_text_layer(tmp_path / "no_text_layer.pdf")
    result = detect_chapters(pdf)
    assert result["source"] == "none"
    assert result["chapters"] == []
    assert result["warnings"]


def test_end_page_is_contiguous(tmp_path):
    pdf = generate_chapters_ok(tmp_path / "chapters_ok.pdf")
    result = detect_chapters(pdf)
    chs = result["chapters"]
    assert len(chs) >= 2
    for a, b in zip(chs, chs[1:]):
        assert a["end_page"] + 1 == b["start_page"], (a, b)
    assert chs[-1]["end_page"] == result["page_count"]


def test_split_empty_chapters_writes_no_pdf(tmp_path):
    pdf = generate_no_text_layer(tmp_path / "empty_src.pdf")
    out = tmp_path / "split_empty"
    manifest = split_by_chapters(
        pdf,
        out,
        {"source": "none", "page_count": 3, "chapters": [], "warnings": ["no text layer"]},
    )
    assert manifest["chapters"] == []
    assert manifest.get("reason")
    assert list(out.glob("*.pdf")) == []
    assert (out / "split-manifest.json").is_file()


def test_split_pdf_page_count_matches_range(tmp_path):
    pdf = generate_chapters_ok(tmp_path / "chapters_ok.pdf")
    detected = detect_chapters(pdf)
    out = tmp_path / "split_ok"
    manifest = split_by_chapters(pdf, out, detected)
    assert manifest["chapters"]
    import fitz

    for item, ch in zip(manifest["chapters"], detected["chapters"]):
        expected = ch["end_page"] - ch["start_page"] + 1
        assert item["page_count"] == expected
        part = fitz.open(item["pdf_path"])
        try:
            assert len(part) == expected
        finally:
            part.close()


def test_front_matter_is_not_merged_into_chapter_one(tmp_path):
    pdf = generate_chapters_ok(tmp_path / "chapters_ok.pdf")
    result = detect_chapters(pdf)
    real = [c for c in result["chapters"] if c["index"] >= 1]
    assert real
    assert real[0]["index"] == 1
    assert real[0]["start_page"] > 1
    front = [c for c in result["chapters"] if c["index"] == 0]
    if front:
        assert front[0]["end_page"] + 1 == real[0]["start_page"]
        assert front[0]["start_page"] == 1


def _real_chapters(result: dict) -> list[dict]:
    return [c for c in result["chapters"] if c["index"] >= 1]


def test_split_heading_two_lines_is_one_chapter(tmp_path):
    pdf = generate_split_heading(tmp_path / "split_heading.pdf")
    result = detect_chapters(pdf)
    real = _real_chapters(result)
    assert len(real) == 1, result
    assert real[0]["title"] == "2 Normative references"


def test_split_heading_mid_page_is_detected(tmp_path):
    pdf = generate_split_heading_midpage(tmp_path / "split_midpage.pdf")
    result = detect_chapters(pdf)
    real = _real_chapters(result)
    assert real, result
    assert any(c["title"] == "3 Terms and definitions" for c in real), result


def test_number_regression_is_dropped(tmp_path):
    pdf = generate_number_regression(tmp_path / "number_regression.pdf")
    result = detect_chapters(pdf)
    titles = [c["title"] for c in _real_chapters(result)]
    assert titles == [
        "1 Alpha Topic",
        "2 Beta Topic",
        "7 Gamma Topic",
        "8 Epsilon Topic",
    ], result
    assert not any(t.startswith("3 ") for t in titles)


def test_numbering_gap_warns_and_does_not_invent(tmp_path):
    pdf = generate_number_gap(tmp_path / "number_gap.pdf")
    result = detect_chapters(pdf)
    titles = [c["title"] for c in _real_chapters(result)]
    assert titles == ["1 First Topic Name", "6 Sixth Topic Name"], result
    assert any("chapter numbering gap: 1 -> 6" in w for w in result["warnings"]), result
    assert not any(t.startswith(f"{n} ") for t in titles for n in (2, 3, 4, 5))


def test_split_headings_on_toc_page_are_excluded(tmp_path):
    pdf = generate_split_toc_page(tmp_path / "split_toc.pdf")
    result = detect_chapters(pdf)
    real = _real_chapters(result)
    assert real, result
    assert all(c["start_page"] != 1 for c in real), result
    assert not any("Alpha Listing" in c["title"] for c in real)


def test_no_text_layer_still_none_after_split_rules(tmp_path):
    pdf = generate_no_text_layer(tmp_path / "no_text_layer_p9.pdf")
    result = detect_chapters(pdf)
    assert result["source"] == "none"
    assert result["chapters"] == []


def test_split_manifest_includes_page_offset(tmp_path):
    pdf = generate_chapters_ok(tmp_path / "chapters_ok.pdf")
    detected = detect_chapters(pdf)
    out = tmp_path / "split_offset"
    manifest = split_by_chapters(pdf, out, detected)
    assert manifest["chapters"]
    for item, ch in zip(manifest["chapters"], detected["chapters"]):
        assert item["page_offset"] == ch["start_page"] - 1
