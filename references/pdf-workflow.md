# PDF high-fidelity workflow (pdf2md)

Offline PDF → structured Markdown subsystem used by `book-to-skill` technical mode.

## Commands

```bash
book-to-skill-pdf2md doctor --json
book-to-skill-pdf2md convert INPUT.pdf --output DIR --profile auto|fast|accurate [--strict]
book-to-skill-pdf2md benchmark --corpus MANIFEST.json --run-dir runs/pdf2md/<id>
book-to-skill-pdf2md optimize --corpus MANIFEST.json --base-ref HEAD --budget 8 [--auto-commit]
```

## Bundle layout

- `document.md` — page markers `<!-- page: N -->`, stable block IDs, tables, figures, formulas
- `document.ir.json` — schema_version, source hash, page types, ordered blocks
- `quality-report.json` — dimension scores, hard gates, versions, hashes
- `assets/{pages,figures,tables,formulas}/` — crops tied to page+bbox

## Page routing

Classify each page as `native-text|scanned|image-based|mixed|broken-encoding`.

- Native text → structured extract + pdfplumber tables
- Scanned / broken encoding / image-based → pypdfium2 render (default 300 DPI) + Tesseract OCR
- OCR languages: IEC=`eng`, SIEMENS=`deu+eng` (auto from path/name)
- Rotation: PDF `/Rotate` combined with Tesseract OSD

## Hard gates (strict mode)

- Page count alignment 100%
- Asset refs valid; no remote URLs
- Network guard active (no outbound connect)
- Non-empty page ratio ≥ 98%
- Single repeated-line ratio < 20% (watermark / garbage rejection)

## Teachers (evidence only)

MarkItDown (local only), pdfminer, pypdf, pdfplumber, AnyDoc, current extractors — never sole ground truth.

## Offline policy

After corpus mount: socket guard + OS isolation (`sandbox-exec` / netns / `docker --network=none`). Optimize fails closed if OS isolation cannot be proven.

## Profiles

Declarative only (`fast` / `accurate` / `auto`). Optimizer v1 may edit profiles, figure prompts, and this reference — not core security code or dependencies.
