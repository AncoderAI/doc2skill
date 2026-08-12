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
