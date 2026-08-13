"""Physically split a PDF into per-chapter files.

An empty chapter list writes no PDFs. Copying the whole book as "chapter 1" is forbidden.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .optimize.net_guard import install_guard

_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def split_by_chapters(pdf_path: Path, out_dir: Path, chapters: dict) -> dict:
    """Write one PDF per chapter. Empty ``chapters`` → empty manifest, no PDFs."""
    install_guard(allow_loopback=True)
    src = Path(pdf_path)
    dest = Path(out_dir)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dest.mkdir(parents=True, exist_ok=True)

    items = list(chapters.get("chapters") or [])
    if not items:
        reason = _empty_reason(chapters)
        manifest = {
            "chapters": [],
            "reason": reason,
            "warnings": list(chapters.get("warnings") or []),
        }
        _write_manifest(dest, manifest)
        return manifest

    import fitz

    doc = fitz.open(src)
    try:
        written: list[dict] = []
        for ch in items:
            start = int(ch["start_page"])
            end = int(ch["end_page"])
            if start < 1 or end < start or end > len(doc):
                raise ValueError(
                    f"invalid chapter page range: index={ch.get('index')} "
                    f"start_page={start} end_page={end} page_count={len(doc)}"
                )
            part = fitz.open()
            part.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
            slug = title_slug(str(ch.get("title") or ""))
            filename = f"{int(ch['index']):02d}_{slug}.pdf"
            pdf_out = dest / filename
            part.save(str(pdf_out))
            page_count = len(part)
            part.close()
            written.append(
                {
                    "index": int(ch["index"]),
                    "title": ch.get("title"),
                    "src_pages": [start, end],
                    "pdf_path": str(pdf_out),
                    "page_count": page_count,
                }
            )
    finally:
        doc.close()

    manifest = {"chapters": written, "reason": None, "warnings": list(chapters.get("warnings") or [])}
    _write_manifest(dest, manifest)
    return manifest


def title_slug(title: str) -> str:
    s = _ILLEGAL_FILENAME.sub("", title)
    s = re.sub(r"\s+", "_", s.strip())
    s = s.strip("._")
    return (s or "chapter")[:80]


def _empty_reason(chapters: dict) -> str:
    warnings = chapters.get("warnings") or []
    if warnings:
        return warnings[0]
    source = chapters.get("source")
    if source == "none":
        return "no chapters detected"
    return "chapters list is empty; not copying the whole PDF"


def _write_manifest(out_dir: Path, manifest: dict) -> None:
    path = out_dir / "split-manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
