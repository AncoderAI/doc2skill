#!/usr/bin/env python3
"""Generate synthetic PDFs for public CI (no private corpus)."""

from __future__ import annotations

from pathlib import Path


def generate_all(out_dir: Path) -> list[Path]:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    # 1) native multi-column-ish text + heading
    p = out_dir / "native_text.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "Chapter 1 Reliability")
    c.setFont("Helvetica", 11)
    y = 690
    for line in [
        "Bounded queues prevent overload in distributed systems.",
        "Backpressure propagates from consumer to producer.",
        "Table of failure modes follows on the next pages.",
    ]:
        c.drawString(72, y, line)
        y -= 16
    c.showPage()
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 720, "1.1 Metrics")
    c.setFont("Helvetica", 11)
    c.drawString(72, 700, "Latency, traffic, errors, and saturation.")
    c.save()
    paths.append(p)

    # 2) table with simple grid (drawn as text table)
    p = out_dir / "table_simple.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(72, 720, "Component FIT Rates")
    data = [
        ["Part", "FIT", "Unit"],
        ["MCU", "10", "FIT"],
        ["Flash", "5", "FIT"],
        ["SRAM", "8", "FIT"],
    ]
    y = 680
    for row in data:
        c.drawString(72, y, f"{row[0]:<12} {row[1]:<8} {row[2]}")
        y -= 18
    # Also draw line grid for pdfplumber
    c.rect(70, 620, 200, 80)
    c.line(70, 680, 270, 680)
    c.line(140, 620, 140, 700)
    c.line(200, 620, 200, 700)
    c.save()
    paths.append(p)

    # 3) formula-like page
    p = out_dir / "formula_page.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Failure rate model")
    c.drawString(72, 700, "lambda = lambda0 * pi_T * pi_E")
    c.drawString(72, 680, "Approximate form: $ \\lambda = \\lambda_0 \\cdot \\pi_T $")
    c.save()
    paths.append(p)

    # 4) repeated watermark text across pages
    p = out_dir / "watermark_repeat.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    for i in range(4):
        c.setFont("Helvetica", 9)
        c.drawString(72, 750, "WATERMARK-CONFIDENTIAL-ACME")
        c.setFont("Helvetica", 12)
        c.drawString(72, 700, f"Real content page {i+1}: derating guidelines.")
        c.drawString(72, 750, "WATERMARK-CONFIDENTIAL-ACME")
        c.showPage()
    c.save()
    paths.append(p)

    # 5) blank-ish page then text (mixed)
    p = out_dir / "mixed_pages.pdf"
    c = canvas.Canvas(str(p), pagesize=letter)
    c.showPage()  # mostly empty
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Recovered text after empty page.")
    c.save()
    paths.append(p)

    # 6) encrypted stub — reportlab can't easily encrypt without owner pwd tools;
    # create a tiny invalid/corrupt pdf for failure path
    p = out_dir / "corrupt.pdf"
    p.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\nstartxref\n0\n%%EOF\nbroken")
    paths.append(p)

    return paths


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    out = root / "tests" / "pdf2md" / "fixtures" / "synthetic"
    print(generate_all(out))
