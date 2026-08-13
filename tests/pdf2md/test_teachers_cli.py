"""Integration-ish tests for teachers and CLI doctor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from book_to_skill.pdf2md.teachers import TEACHERS, convert, probe

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def test_teacher_probes():
    for tool in TEACHERS:
        info = probe(tool)
        assert info["tool"] == tool
        assert info["status"] in {"ok", "unsupported"}


def test_teachers_on_native_pdf():
    pdf = FIXTURES / "native_text.pdf"
    for tool in ("pypdf", "pdfminer", "pdfplumber", "current"):
        result = convert(tool, pdf)
        assert result.status in {"ok", "failed", "unsupported"}
        if result.status == "ok":
            assert "Reliability" in result.text or len(result.text) > 10


def test_cli_doctor_json():
    proc = subprocess.run(
        [sys.executable, "-m", "book_to_skill.pdf2md.cli", "doctor", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1)
    data = json.loads(proc.stdout)
    assert "net_guard" in data
    assert "packages" in data
    assert data["packages"]["img2table"].get("optional") is True
    assert "installed" in data["packages"]["img2table"]
    assert data["packages"]["opencv-python-headless"].get("optional") is True
    assert "installed" in data["packages"]["opencv-python-headless"]
    # Missing optional CV stack must not fail doctor; only hint.
    if (
        not data["packages"]["img2table"]["installed"]
        or not data["packages"]["opencv-python-headless"]["installed"]
    ):
        assert any("optional_img2table_missing" in h for h in data.get("hints") or [])
        assert "optional_img2table_missing" not in (data.get("issues") or [])


def test_doctor_optional_img2table_hint_does_not_fail(monkeypatch):
    from book_to_skill.pdf2md import doctor as doctor_mod

    real_pkg = doctor_mod._pkg

    def fake_pkg(name: str):
        if name in {"img2table", "opencv-python-headless", "opencv-python"}:
            return {"installed": False, "version": None}
        return real_pkg(name)

    monkeypatch.setattr(doctor_mod, "_pkg", fake_pkg)
    monkeypatch.setattr(
        doctor_mod,
        "_optional_opencv",
        lambda: {
            "installed": False,
            "version": None,
            "optional": True,
            "import_name": "cv2",
            "dist": "opencv-python-headless",
        },
    )
    report = doctor_mod.run_doctor()
    assert report["packages"]["img2table"]["optional"] is True
    assert report["packages"]["img2table"]["installed"] is False
    assert any("optional_img2table_missing" in h for h in report["hints"])
    assert "optional_img2table_missing" not in report["issues"]
    # Doctor ok may still be False for unrelated hard issues; optional must not add one.
    assert not any("img2table" in i for i in report["issues"])


def test_cli_convert_native(tmp_path):
    pdf = FIXTURES / "native_text.pdf"
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "book_to_skill.pdf2md.cli",
            "convert",
            str(pdf),
            "--output",
            str(out),
            "--profile",
            "fast",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "document.md").exists()
