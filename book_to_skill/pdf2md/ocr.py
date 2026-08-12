"""Tesseract OCR + OSD helpers (offline)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional

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
