"""Tesseract OCR + OSD helpers (offline)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

# Pillow is only referenced in annotations here (callers pass the image in, and
# `from __future__ import annotations` keeps these lazy). Importing it eagerly
# made `pdf2md doctor` — the command whose job is to report missing extras —
# crash with ModuleNotFoundError on machines that lack them.
if TYPE_CHECKING:
    from PIL import Image


class OCRError(RuntimeError):
    pass


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def list_langs() -> set:
    if not tesseract_available():
        return set()
    proc = subprocess.run(
        ["tesseract", "--list-langs"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [
        ln.strip()
        for ln in (proc.stdout + "\n" + proc.stderr).splitlines()
        if ln.strip() and not ln.lower().startswith("list of")
    ]
    return set(lines)


def ocr_image(
    image: Image.Image,
    *,
    lang: str = "eng",
    psm: int = 3,
    dpi: int = 300,
) -> str:
    """OCR a PIL image; returns plain text (never fabricates success on failure)."""
    if not tesseract_available():
        raise OCRError("tesseract binary not found")
    with tempfile.TemporaryDirectory(prefix="pdf2md_ocr_") as tmp:
        img_path = Path(tmp) / "page.png"
        image.save(img_path, format="PNG", dpi=(dpi, dpi))
        proc = subprocess.run(
            [
                "tesseract",
                str(img_path),
                "stdout",
                "-l",
                lang,
                "--psm",
                str(psm),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 and not (proc.stdout or "").strip():
            raise OCRError(
                f"tesseract failed rc={proc.returncode}: {(proc.stderr or '').strip()}"
            )
        return proc.stdout or ""


def ocr_image_words(
    image: Image.Image,
    *,
    lang: str = "eng",
    psm: int = 3,
    dpi: int = 300,
    page_size_pts: Optional[tuple] = None,
) -> list:
    """OCR word boxes as [(bbox_pts, text), ...] in PDF bottom-left coordinates.

    ``page_size_pts`` is ``(width, height)`` in PDF points. Required to map
    tesseract's top-left pixel boxes into PDF space. Raises OCRError on failure.
    """
    if not tesseract_available():
        raise OCRError("tesseract binary not found")
    if page_size_pts is None:
        raise OCRError("page_size_pts required for word box mapping")
    page_h = float(page_size_pts[1])
    scale = dpi / 72.0
    with tempfile.TemporaryDirectory(prefix="pdf2md_ocr_tsv_") as tmp:
        img_path = Path(tmp) / "page.png"
        image.save(img_path, format="PNG", dpi=(dpi, dpi))
        out_base = Path(tmp) / "out"
        proc = subprocess.run(
            [
                "tesseract",
                str(img_path),
                str(out_base),
                "-l",
                lang,
                "--psm",
                str(psm),
                "tsv",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        tsv_path = Path(str(out_base) + ".tsv")
        if not tsv_path.is_file():
            raise OCRError(
                f"tesseract tsv missing rc={proc.returncode}: {(proc.stderr or '').strip()}"
            )
        lines = tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            return []
        header = lines[0].split("\t")
        idx = {name: i for i, name in enumerate(header)}
        needed = ("level", "left", "top", "width", "height", "text")
        if any(n not in idx for n in needed):
            raise OCRError(f"tesseract tsv missing columns: {header}")
        words = []
        for row in lines[1:]:
            cols = row.split("\t")
            if len(cols) <= idx["text"]:
                continue
            try:
                level = int(cols[idx["level"]])
            except ValueError:
                continue
            if level != 5:  # word
                continue
            text = cols[idx["text"]].strip()
            if not text:
                continue
            left = float(cols[idx["left"]])
            top = float(cols[idx["top"]])
            width = float(cols[idx["width"]])
            height = float(cols[idx["height"]])
            x0 = left / scale
            x1 = (left + width) / scale
            y1 = page_h - (top / scale)
            y0 = page_h - ((top + height) / scale)
            if y1 < y0:
                y0, y1 = y1, y0
            words.append(((x0, y0, x1, y1), text))
        return words


def osd_image(image: Image.Image, *, dpi: int = 300) -> Dict[str, object]:
    """Run tesseract OSD (--psm 0). Raises OCRError if orientation cannot be read."""
    if not tesseract_available():
        raise OCRError("tesseract binary not found")
    with tempfile.TemporaryDirectory(prefix="pdf2md_osd_") as tmp:
        img_path = Path(tmp) / "page.png"
        image.save(img_path, format="PNG", dpi=(dpi, dpi))
        proc = subprocess.run(
            ["tesseract", str(img_path), "stdout", "--psm", "0"],
            capture_output=True,
            text=True,
            check=False,
        )
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if "Orientation in degrees" not in combined:
            raise OCRError(
                f"OSD failed rc={proc.returncode}: {(proc.stderr or '').strip()}"
            )
        return _parse_osd(combined)


def _parse_osd(text: str) -> Dict[str, object]:
    patterns = {
        "orientation_deg": (r"Orientation in degrees:\s*(\d+)", int),
        "rotate": (r"Rotate:\s*(\d+)", int),
        "orientation_conf": (r"Orientation confidence:\s*([0-9.]+)", float),
        "script": (r"Script:\s*(\S+)", str),
        "script_conf": (r"Script confidence:\s*([0-9.]+)", float),
    }
    out: Dict[str, object] = {}
    for key, (pat, cast) in patterns.items():
        m = re.search(pat, text)
        if not m:
            raise OCRError(f"OSD missing field {key}")
        out[key] = cast(m.group(1))
    return out


def resolve_rotation(pdf_rotate: int, osd: Optional[Dict[str, object]]) -> int:
    """Combine PDF /Rotate with OSD rotate suggestion (degrees clockwise to upright)."""
    pdf_rotate = int(pdf_rotate or 0) % 360
    if not osd:
        return pdf_rotate
    osd_rot = int(osd.get("rotate") or 0) % 360
    # Prefer OSD when confidence is meaningful; else PDF metadata.
    conf = float(osd.get("orientation_conf") or 0.0)
    if conf >= 2.0 and osd_rot:
        return osd_rot
    return pdf_rotate or osd_rot
