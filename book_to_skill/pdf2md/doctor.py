"""Doctor checks for pdf2md offline readiness."""

from __future__ import annotations

import json
import shutil
import socket
import sys
from importlib import metadata
from typing import Any, Dict, List

from .ocr import list_langs, tesseract_available
from .optimize.net_guard import NetworkBlocked, install_guard, is_active, uninstall_guard


def _pkg(name: str) -> Dict[str, Any]:
    try:
        return {"installed": True, "version": metadata.version(name)}
    except metadata.PackageNotFoundError:
        return {"installed": False, "version": None}


def _optional_pkg(name: str) -> Dict[str, Any]:
    info = _pkg(name)
    info["optional"] = True
    return info


def _optional_opencv() -> Dict[str, Any]:
    """Probe opencv-python-headless (preferred) or opencv-python; both provide cv2."""
    for dist in ("opencv-python-headless", "opencv-python"):
        info = _pkg(dist)
        if info["installed"]:
            info["optional"] = True
            info["import_name"] = "cv2"
            info["dist"] = dist
            return info
    try:
        import cv2  # type: ignore[import-untyped]

        return {
            "installed": True,
            "version": getattr(cv2, "__version__", None),
            "optional": True,
            "import_name": "cv2",
            "dist": "cv2-import",
        }
    except ImportError:
        return {
            "installed": False,
            "version": None,
            "optional": True,
            "import_name": "cv2",
            "dist": "opencv-python-headless",
        }


def run_doctor() -> Dict[str, Any]:
    bins = {
        "tesseract": shutil.which("tesseract"),
        "gs": shutil.which("gs"),
        "pdftotext": shutil.which("pdftotext"),
    }
    langs = sorted(list_langs()) if tesseract_available() else []
    packages = {
        "pypdfium2": _pkg("pypdfium2"),
        "pdfplumber": _pkg("pdfplumber"),
        "pypdf": _pkg("pypdf"),
        "Pillow": _pkg("Pillow"),
        "pytesseract": _pkg("pytesseract"),
        "docling": _pkg("docling"),
        "markitdown": _pkg("markitdown"),
        "firecrawl-anydoc": _pkg("firecrawl-anydoc"),
        # Optional: scanned / borderless table CV path (not a hard dependency).
        "img2table": _optional_pkg("img2table"),
        "opencv-python-headless": _optional_opencv(),
    }

    # Prove net guard
    net_ok = False
    net_error = None
    try:
        install_guard(allow_loopback=True)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(("1.1.1.1", 443))
            net_error = "guard_did_not_block"
        except NetworkBlocked:
            net_ok = True
        finally:
            sock.close()
            uninstall_guard()
    except Exception as exc:  # noqa: BLE001
        net_error = f"{type(exc).__name__}: {exc}"
        uninstall_guard()

    issues: List[str] = []
    if not bins["tesseract"]:
        issues.append("tesseract_missing")
    if not packages["pypdfium2"]["installed"]:
        issues.append("pypdfium2_missing")
    if not packages["pdfplumber"]["installed"]:
        issues.append("pdfplumber_missing")
    if not packages["pypdf"]["installed"]:
        issues.append("pypdf_missing")
    if "eng" not in langs:
        issues.append("tesseract_lang_eng_missing")
    if not net_ok:
        issues.append("net_guard_failed")

    # Optional deps: never fail doctor, but surface a clear capability gap.
    hints: List[str] = []
    if not packages["img2table"]["installed"] or not packages["opencv-python-headless"]["installed"]:
        hints.append(
            "optional_img2table_missing: scanned-page table extraction needs "
            "img2table + opencv-python-headless (cv2). Without them the pipeline "
            "falls back to OCR word-box projection, which often returns 0 tables "
            "on borderless scans (e.g. SIEMENS). "
            "Install: pip install 'book-to-skill[pdf2md-scan-tables]'"
        )

    licenses: Dict[str, Any] = {}
    for name, info in packages.items():
        if not info["installed"]:
            continue
        if name == "opencv-python-headless":
            dist = info.get("dist") or "opencv-python-headless"
            if dist == "cv2-import":
                continue
            licenses[dist] = _license(dist)
        else:
            licenses[name] = _license(name)

    return {
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "binaries": bins,
        "tesseract_langs": langs,
        "packages": packages,
        "net_guard": {"ok": net_ok, "error": net_error, "active_now": is_active()},
        "licenses": licenses,
        "ok": len(issues) == 0,
        "issues": issues,
        "hints": hints,
    }


def _license(name: str) -> str | None:
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return None
    expr = meta.get("License-Expression")
    if expr:
        return expr
    lic = meta.get("License")
    if lic and len(lic) < 200:
        return lic
    classifiers = meta.get_all("Classifier") or []
    lines = [c for c in classifiers if c.startswith("License ::")]
    return "; ".join(lines) if lines else (lic[:120] if lic else None)


def doctor_json() -> str:
    return json.dumps(run_doctor(), indent=2, ensure_ascii=False) + "\n"
