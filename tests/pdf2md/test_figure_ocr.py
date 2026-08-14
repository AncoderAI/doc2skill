"""Per-crop OCR into FigureBlock.ocr_labels (the model-independent signal)."""

from __future__ import annotations

import pytest
from PIL import Image

from book_to_skill.pdf2md import figures as figures_mod
from book_to_skill.pdf2md.figures import figure_from_image, ocr_figure_asset
from book_to_skill.pdf2md.profiles import resolve_profile


def _blank(w: int = 40, h: int = 20) -> Image.Image:
    return Image.new("RGB", (w, h), "white")


def test_fast_profile_leaves_figure_ocr_off():
    """P8's convert budget is not spent OCRing crops unless asked."""
    assert resolve_profile("fast").enable_figure_ocr is False
    assert resolve_profile("accurate").enable_figure_ocr is True


def test_tesseract_missing_returns_warning_not_raise(monkeypatch):
    monkeypatch.setattr(
        "book_to_skill.pdf2md.ocr.tesseract_available", lambda: False
    )
    text, warning = ocr_figure_asset(_blank(), lang="eng", psm=6, dpi=150)
    assert text == ""
    assert warning and "tesseract" in warning


def test_tesseract_failure_is_not_swallowed(monkeypatch):
    """A present-but-failing tesseract must surface, not look like 'no text'."""
    monkeypatch.setattr(
        "book_to_skill.pdf2md.ocr.tesseract_available", lambda: True
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("tesseract exploded")

    monkeypatch.setattr("book_to_skill.pdf2md.ocr.ocr_image", boom)
    with pytest.raises(RuntimeError):
        ocr_figure_asset(_blank(), lang="eng", psm=6, dpi=150)


def test_ocr_text_reaches_ocr_labels():
    block = figure_from_image(
        "assets/figures/a.png", ocr_text="Input\nSolver\nOutput"
    )
    assert block.ocr_labels == ["Input", "Solver", "Output"]


def test_no_ocr_text_leaves_labels_empty():
    """`fast` must not fabricate labels — an empty crop signal stays empty."""
    block = figure_from_image("assets/figures/a.png", ocr_text="")
    assert block.ocr_labels == []
    assert block.description is None


def test_ocr_figure_asset_is_offline(monkeypatch):
    """No network: the guard stays armed through a crop OCR call."""
    from book_to_skill.pdf2md.optimize.net_guard import (
        NetworkBlocked,
        install_guard,
        uninstall_guard,
    )

    monkeypatch.setattr(
        "book_to_skill.pdf2md.ocr.tesseract_available", lambda: False
    )
    uninstall_guard()
    install_guard(allow_loopback=True)
    try:
        text, _ = ocr_figure_asset(_blank(), lang="eng", psm=6, dpi=150)
        assert text == ""
    except NetworkBlocked:  # pragma: no cover - would be a real defect
        pytest.fail("figure OCR must not open a socket")
    finally:
        uninstall_guard()


def test_module_exposes_ocr_entry_point():
    assert hasattr(figures_mod, "ocr_figure_asset")
