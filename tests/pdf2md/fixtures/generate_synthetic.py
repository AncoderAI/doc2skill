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

    paths.append(generate_header_repeat(out_dir / "header_repeat.pdf"))
    paths.append(generate_toc_page(out_dir / "toc_page.pdf"))
    paths.append(generate_bad_toc_outline(out_dir / "bad_toc_outline.pdf"))
    paths.append(generate_no_text_layer(out_dir / "no_text_layer.pdf"))
    paths.append(generate_chapters_ok(out_dir / "chapters_ok.pdf"))

    return paths


def generate_header_repeat(path: Path) -> Path:
    """Same 'Chapter 1' at the same y on ≥3 pages — a running header, not chapter starts."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(4):
        c.setFont("Helvetica", 10)
        c.drawString(72, 750, "Chapter 1")
        c.setFont("Helvetica", 12)
        c.drawString(72, 680, f"Body paragraph on page {i + 1}.")
        c.showPage()
    c.save()
    return path


def generate_toc_page(path: Path) -> Path:
    """Page 1 lists ≥3 chapter titles (contents). Real chapter starts are later."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    # p1: contents listing — three different chapter titles on one page
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 720, "Contents")
    c.setFont("Helvetica", 12)
    c.drawString(72, 680, "Chapter 1 Introduction")
    c.drawString(72, 660, "Chapter 2 Methods")
    c.drawString(72, 640, "Chapter 3 Results")
    c.showPage()
    # p2: filler / front matter
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Preface material before any chapter.")
    c.showPage()
    # p3: real chapter 1
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "Chapter 1 Introduction")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, "Body of chapter one.")
    c.showPage()
    # p4: still chapter 1
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "More of chapter one.")
    c.showPage()
    # p5: real chapter 2
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 720, "Chapter 2 Methods")
    c.setFont("Helvetica", 12)
    c.drawString(72, 690, "Body of chapter two.")
    c.showPage()
    # p6: still chapter 2
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "More of chapter two.")
    c.showPage()
    c.save()
    return path


def generate_bad_toc_outline(path: Path) -> Path:
    """Embedded outline is all L1 and starts late — must be rejected, fall back to headings."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(12):
        page = i + 1
        c.setFont("Helvetica", 12)
        c.drawString(72, 680, f"Body page {page}.")
        if page == 3:
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, 720, "Chapter 1 Alpha")
        elif page == 8:
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, 720, "Chapter 2 Beta")
        else:
            c.setFont("Helvetica", 12)
            c.drawString(72, 720, f"Running text page {page}.")
        # Bookmarks all level 0 (= PyMuPDF level 1), first at page 5 (> 12*0.1)
        if page == 5:
            c.bookmarkPage("e1")
            c.addOutlineEntry("1.5.1 Windows install", "e1", level=0)
        elif page == 8:
            c.bookmarkPage("e2")
            c.addOutlineEntry("1.5.2 License server", "e2", level=0)
        elif page == 10:
            c.bookmarkPage("e3")
            c.addOutlineEntry("1.5.3 Client config", "e3", level=0)
        c.showPage()
    c.save()
    return path


def generate_no_text_layer(path: Path) -> Path:
    """Pages with drawings only — no text operators."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for _ in range(3):
        c.rect(72, 72, 200, 200)
        c.line(72, 72, 272, 272)
        c.showPage()
    c.save()
    return path


def generate_chapters_ok(path: Path) -> Path:
    """Front matter on p1-2, chapter 1 on p3-5, chapter 2 on p6-8."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(8):
        page = i + 1
        if page <= 2:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(72, 720, "Preface")
            c.setFont("Helvetica", 12)
            c.drawString(72, 690, f"Front matter page {page}.")
        elif page == 3:
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, 720, "Chapter 1 First topic")
            c.setFont("Helvetica", 12)
            c.drawString(72, 690, "Start of chapter one.")
        elif page <= 5:
            c.setFont("Helvetica", 12)
            c.drawString(72, 720, f"Chapter one continues on page {page}.")
        elif page == 6:
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, 720, "Chapter 2 Second topic")
            c.setFont("Helvetica", 12)
            c.drawString(72, 690, "Start of chapter two.")
        else:
            c.setFont("Helvetica", 12)
            c.drawString(72, 720, f"Chapter two continues on page {page}.")
        c.showPage()
    c.save()
    return path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[3]
    out = root / "tests" / "pdf2md" / "fixtures" / "synthetic"
    print(generate_all(out))
