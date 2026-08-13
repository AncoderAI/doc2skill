"""P8: PDF handle reuse — output unchanged, cache released, no full-page materialization."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from book_to_skill.pdf2md.convert import _extract_native_text, convert_pdf
from book_to_skill.pdf2md.figures import detect_raster_figures
from book_to_skill.pdf2md.handles import _CACHE, cache_key, close_all
from book_to_skill.pdf2md.tables import extract_tables_pdfplumber

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


@pytest.fixture(autouse=True)
def _release_handles():
    yield
    close_all()


def _write_text_pdf(path: Path, line: str, *, pages: int = 1) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(pages):
        c.setFont("Helvetica", 12)
        c.drawString(72, 720, f"{line} page {i + 1}")
        c.showPage()
    c.save()
    return path


def test_output_byte_identical_across_two_converts(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    pdf = FIXTURES / "native_text.pdf"
    a = tmp_path / "a"
    b = tmp_path / "b"
    convert_pdf(pdf, a, profile="fast")
    convert_pdf(pdf, b, profile="fast")
    assert (a / "document.md").read_bytes() == (b / "document.md").read_bytes()
    assert (a / "document.ir.json").read_bytes() == (b / "document.ir.json").read_bytes()


def test_convert_pdf_clears_handle_cache(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    convert_pdf(FIXTURES / "native_text.pdf", tmp_path / "out", profile="fast")
    assert len(_CACHE) == 0


def test_exception_path_clears_handle_cache(tmp_path, monkeypatch):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    import book_to_skill.pdf2md.assemble as assemble_mod

    def boom(*_a, **_k):
        assert len(_CACHE) > 0
        raise RuntimeError("injected-failure")

    monkeypatch.setattr(assemble_mod, "assemble_markdown", boom)
    with pytest.raises(RuntimeError, match="injected-failure"):
        convert_pdf(FIXTURES / "native_text.pdf", tmp_path / "out", profile="fast")
    assert len(_CACHE) == 0


def test_cache_key_includes_mtime(tmp_path):
    pytest.importorskip("pypdf")
    pdf = tmp_path / "swap.pdf"
    _write_text_pdf(pdf, "ALPHA_UNIQUE_TOKEN")
    key1 = cache_key(pdf)
    text1 = _extract_native_text(str(pdf), 0)
    assert "ALPHA_UNIQUE_TOKEN" in text1
    assert key1 in _CACHE

    _write_text_pdf(pdf, "BETA_UNIQUE_TOKEN")
    st = pdf.stat()
    Path(pdf).touch()
    # Force mtime forward even if the rewrite landed in the same ns bucket.
    import os

    os.utime(pdf, ns=(st.st_mtime_ns + 10**9, st.st_mtime_ns + 10**9))
    key2 = cache_key(pdf)
    assert key2 != key1
    assert key2[0] == key1[0]
    text2 = _extract_native_text(str(pdf), 0)
    assert "BETA_UNIQUE_TOKEN" in text2
    assert "ALPHA_UNIQUE_TOKEN" not in text2


def test_does_not_materialize_all_pages(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("pdfplumber")
    from pdfminer.pdfpage import PDFPage

    n_pages = 20
    pdf = _write_text_pdf(tmp_path / "many.pdf", "Native body text", pages=n_pages)

    inits = {"n": 0}
    orig_init = PDFPage.__init__

    def counting_init(self, *args, **kwargs):
        inits["n"] += 1
        return orig_init(self, *args, **kwargs)

    with patch.object(PDFPage, "__init__", counting_init), patch.object(
        PDFPage, "create_pages", wraps=PDFPage.create_pages
    ) as create_pages:
        convert_pdf(
            pdf,
            tmp_path / "out",
            profile="fast",
            profile_overrides={"page_filter": [1]},
        )

    assert create_pages.call_count == 0
    assert inits["n"] < n_pages
    assert inits["n"] <= 2


def test_out_of_range_page_returns_empty():
    pytest.importorskip("pdfplumber")
    pdf = str(FIXTURES / "native_text.pdf")
    assert extract_tables_pdfplumber(pdf, -1) == []
    assert extract_tables_pdfplumber(pdf, 999) == []
    assert detect_raster_figures(pdf, 999, 1000, 612.0, 792.0) == []
    assert detect_raster_figures(pdf, -1, 0, 612.0, 792.0) == []
