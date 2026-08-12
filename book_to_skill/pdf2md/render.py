"""PDF page rendering via pypdfium2 (product renderer)."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

from PIL import Image


def render_page(
    pdf_path: Union[str, Path],
    page_index: int,
    *,
    dpi: int = 300,
    rotation: int = 0,
) -> Image.Image:
    """Render 0-based page_index to a PIL image at the given DPI."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        scale = dpi / 72.0
        bitmap = page.render(scale=scale, rotation=rotation)
        return bitmap.to_pil()
    finally:
        doc.close()


def page_size(
    pdf_path: Union[str, Path], page_index: int
) -> Tuple[float, float, int]:
    """Return (width_pt, height_pt, rotate_deg) for 0-based page."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    page = reader.pages[page_index]
    box = page.mediabox
    width = float(box.width)
    height = float(box.height)
    rotate = int(page.get("/Rotate") or 0) % 360
    return width, height, rotate


def save_page_png(
    image: Image.Image,
    dest: Union[str, Path],
    *,
    dpi: int = 300,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, format="PNG", dpi=(dpi, dpi))
    return dest
