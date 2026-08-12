"""Teacher adapters for differential evidence (not ground truth)."""

from __future__ import annotations

import hashlib
import time
import traceback
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


@dataclass
class TeacherResult:
    tool: str
    version: Optional[str]
    status: str  # ok|failed|unsupported
    error: Optional[str]
    elapsed_sec: float
    text: str
    sha256: str
    meta: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "version": self.version,
            "status": self.status,
            "error": self.error,
            "elapsed_sec": self.elapsed_sec,
            "chars": len(self.text),
            "sha256": self.sha256,
            "meta": self.meta,
        }


def probe(tool: str) -> Dict[str, Any]:
    """Return availability probe for a teacher tool."""
    mapping = {
        "markitdown": ("markitdown", _convert_markitdown),
        "pdfminer": ("pdfminer.six", _convert_pdfminer),
        "pypdf": ("pypdf", _convert_pypdf),
        "pdfplumber": ("pdfplumber", _convert_pdfplumber),
        "anydoc": ("firecrawl-anydoc", _convert_anydoc),
        "current": (None, _convert_current),
    }
    if tool not in mapping:
        return {"tool": tool, "status": "unsupported", "error": "unknown tool"}
    pkg, _fn = mapping[tool]
    version = None
    if pkg:
        try:
            version = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            return {"tool": tool, "status": "unsupported", "error": f"{pkg} not installed", "version": None}
    return {"tool": tool, "status": "ok", "version": version, "error": None}


def convert(tool: str, pdf_path: Path) -> TeacherResult:
    mapping: Dict[str, Tuple[Optional[str], Callable[[Path], str]]] = {
        "markitdown": ("markitdown", _convert_markitdown),
        "pdfminer": ("pdfminer.six", _convert_pdfminer),
        "pypdf": ("pypdf", _convert_pypdf),
        "pdfplumber": ("pdfplumber", _convert_pdfplumber),
        "anydoc": ("firecrawl-anydoc", _convert_anydoc),
        "current": (None, _convert_current),
    }
    if tool not in mapping:
        return TeacherResult(tool, None, "unsupported", "unknown tool", 0.0, "", _sha(""), {})
    pkg, fn = mapping[tool]
    version = None
    if pkg:
        try:
            version = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            return TeacherResult(tool, None, "unsupported", f"{pkg} not installed", 0.0, "", _sha(""), {})
    t0 = time.perf_counter()
    try:
        text = fn(pdf_path)
        elapsed = time.perf_counter() - t0
        return TeacherResult(tool, version, "ok", None, elapsed, text, _sha(text), {})
    except Exception as exc:  # noqa: BLE001 — record failed
        elapsed = time.perf_counter() - t0
        status = "unsupported" if type(exc).__name__ in {"UnsupportedError", "ImportError"} else "failed"
        return TeacherResult(
            tool,
            version,
            status,
            f"{type(exc).__name__}: {exc}",
            elapsed,
            "",
            _sha(""),
            {"traceback": traceback.format_exc()[-500:]},
        )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _convert_markitdown(pdf: Path) -> str:
    from markitdown import MarkItDown

    md = MarkItDown(enable_plugins=False)
    if hasattr(md, "convert_local"):
        result = md.convert_local(str(pdf))
    else:
        result = md.convert(str(pdf))
    return getattr(result, "text_content", None) or str(result) or ""


def _convert_pdfminer(pdf: Path) -> str:
    from pdfminer.high_level import extract_text

    return extract_text(str(pdf)) or ""


def _convert_pypdf(pdf: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _convert_pdfplumber(pdf: Path) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(str(pdf)) as doc:
        for page in doc.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _convert_anydoc(pdf: Path) -> str:
    import anydoc

    return anydoc.to_markdown(str(pdf)) or ""


def _convert_current(pdf: Path) -> str:
    from book_to_skill.parsers import pdf as pdf_parser

    for fn in (
        pdf_parser.extract_with_pdftotext,
        pdf_parser.extract_with_pypdf,
        pdf_parser.extract_with_pdfminer,
    ):
        text = fn(str(pdf))
        if text and text.strip():
            return text
    return ""


TEACHERS = ["markitdown", "pdfminer", "pypdf", "pdfplumber", "anydoc", "current"]
