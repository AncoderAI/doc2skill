"""P7: chapter detection + physical PDF split."""

from __future__ import annotations

from book_to_skill.pdf2md.chapters import detect_chapters
from book_to_skill.pdf2md.split import split_by_chapters

from tests.pdf2md.fixtures.generate_synthetic import (
    generate_bad_toc_outline,
    generate_chapters_ok,
    generate_header_repeat,
    generate_no_text_layer,
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
