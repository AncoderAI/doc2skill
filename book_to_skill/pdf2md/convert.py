"""Local high-fidelity PDF→IR conversion engine (pypdfium2 + tesseract + pdfplumber).

Docling is used when importable; otherwise this local engine is the product path.
Remote services are never enabled.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .classify import (
    classify_page,
    looks_like_heading,
    repeated_line_candidates,
    strip_watermarks,
)
from .figures import (
    detect_formula_candidates,
    figure_from_image,
    formula_failure,
    formula_from_latex,
)
from .ir import (
    SCHEMA_VERSION,
    Block,
    BlockType,
    DocumentIR,
    PageInfo,
    PageType,
    file_sha256,
    stable_block_id,
)
from .ocr import OCRError, ocr_image, osd_image, resolve_rotation, tesseract_available
from .optimize.net_guard import install_guard, is_active
from .profiles import ConvertProfile, default_ocr_lang_for_path, resolve_profile
from .quality import build_quality_report
from .render import page_size, render_page, save_page_png
from .tables import extract_tables_pdfplumber


def _tool_versions() -> Dict[str, Any]:
    out: Dict[str, Any] = {"tesseract": None, "pypdfium2": None, "pdfplumber": None, "pypdf": None, "docling": None}
    try:
        import shutil
        import subprocess

        if shutil.which("tesseract"):
            proc = subprocess.run(
                ["tesseract", "--version"], capture_output=True, text=True, check=False
            )
            out["tesseract"] = (proc.stdout or proc.stderr or "").splitlines()[0]
    except Exception as exc:  # noqa: BLE001
        out["tesseract"] = f"error:{type(exc).__name__}"
    for name in ("pypdfium2", "pdfplumber", "pypdf", "docling"):
        try:
            from importlib import metadata

            out[name] = metadata.version(name)
        except Exception:
            out[name] = None
    return out


def _extract_native_text(pdf_path: str, page_index: int) -> str:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    page = reader.pages[page_index]
    return page.extract_text() or ""


def _embedded_image_count(pdf_path: str, page_index: int) -> int:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    page = reader.pages[page_index]
    try:
        resources = page.get("/Resources")
        if resources is None:
            return 0
        resources = resources.get_object()
        xobj = resources.get("/XObject")
        if xobj is None:
            return 0
        xobj = xobj.get_object()
        n = 0
        for _name, ref in xobj.items():
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                n += 1
        return n
    except Exception:
        return 0


def _page_count(pdf_path: str) -> int:
    from pypdf import PdfReader

    return len(PdfReader(pdf_path).pages)


def _text_to_blocks(
    page: int,
    text: str,
    *,
    start_index: int = 0,
) -> List[Block]:
    blocks: List[Block] = []
    buf: List[str] = []
    idx = start_index

    def flush_text() -> None:
        nonlocal idx
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        blocks.append(
            Block(
                block_id=stable_block_id(page, idx, "text"),
                type=BlockType.TEXT,
                page=page,
                text=body,
            )
        )
        idx += 1

    for ln in text.splitlines():
        if looks_like_heading(ln):
            flush_text()
            blocks.append(
                Block(
                    block_id=stable_block_id(page, idx, "heading"),
                    type=BlockType.HEADING,
                    page=page,
                    text=ln.strip(),
                    level=2,
                )
            )
            idx += 1
        else:
            buf.append(ln)
    flush_text()
    return blocks


def convert_pdf(
    input_path: Union[str, Path],
    output_dir: Union[str, Path],
    *,
    profile: str = "auto",
    strict: bool = False,
    profile_overrides: Optional[Dict[str, Any]] = None,
    install_network_guard: bool = True,
) -> Dict[str, Any]:
    """Convert PDF to pdf2md bundle. Returns quality report dict.

    On hard-gate failure with ``strict=True``, raises ``SystemExit``-style via
    returning report with passed=False; caller CLI maps to nonzero exit.
    """
    t0 = time.perf_counter()
    if install_network_guard:
        install_guard(allow_loopback=True)

    pdf_path = Path(input_path).resolve()
    out = Path(output_dir).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(str(pdf_path))

    prof = resolve_profile(profile, profile_overrides)
    if not prof.ocr_lang or prof.ocr_lang == "eng":
        # auto-pick corpus language defaults when still default eng
        auto_lang = default_ocr_lang_for_path(str(pdf_path))
        if profile == "auto" or "siemens" in str(pdf_path).lower() or "iec" in str(pdf_path).lower():
            prof.ocr_lang = auto_lang

    # Prefer docling when available and profile is accurate/auto
    engine_name = "local"
    ir: Optional[DocumentIR] = None
    try:
        if profile in ("accurate", "auto"):
            ir = _try_docling(pdf_path, out, prof)
            if ir is not None:
                engine_name = "docling"
    except Exception:  # noqa: BLE001 — record, fall back
        # Docling optional; never pretend success
        pass

    if ir is None:
        ir = _convert_local(pdf_path, out, prof)
        engine_name = "local"
    ir.engine = engine_name
    ir.profile = prof.name

    elapsed = time.perf_counter() - t0
    # Write markdown first so quality can hash artifacts
    from .assemble import assemble_markdown
    import json

    out.mkdir(parents=True, exist_ok=True)
    for sub in ("pages", "figures", "tables", "formulas"):
        (out / "assets" / sub).mkdir(parents=True, exist_ok=True)
    (out / "document.md").write_text(assemble_markdown(ir), encoding="utf-8")
    ir.to_json(out / "document.ir.json")

    report = build_quality_report(
        ir,
        out,
        elapsed_sec=elapsed,
        peak_memory_mb=None,
        config=prof.to_dict(),
        tool_versions=_tool_versions(),
        network_blocked=is_active(),
    )
    (out / "quality-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if strict and not report.get("passed", False):
        report["strict_failed"] = True
    return report


def _try_docling(pdf_path: Path, out: Path, prof: ConvertProfile) -> Optional[DocumentIR]:
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat
    except ImportError:
        return None

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    # Explicitly no remote services — attribute may vary by version
    for attr in ("enable_remote_services", "do_picture_description"):
        if hasattr(pipeline_options, attr):
            setattr(pipeline_options, attr, False if "remote" in attr else prof.enable_figures)

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )
    result = converter.convert(str(pdf_path))
    md = result.document.export_to_markdown()
    page_count = _page_count(str(pdf_path))
    ir = DocumentIR(
        schema_version=SCHEMA_VERSION,
        source_path=str(pdf_path),
        source_sha256=file_sha256(pdf_path),
        page_count=page_count,
        profile=prof.name,
        engine="docling",
    )
    # Represent as single-stream text blocks per page marker if present
    pages = re.split(r"(?m)^<!--\s*page:\s*(\d+)\s*-->\s*$", md)
    if len(pages) == 1:
        ir.pages = [
            PageInfo(
                page=i + 1,
                width=0,
                height=0,
                rotation=0,
                page_type=PageType.NATIVE_TEXT,
            )
            for i in range(page_count)
        ]
        ir.blocks.extend(_text_to_blocks(1, md))
    # Always also run local enrichment for tables on native pages is skipped when docling works
    # Ensure page infos exist
    if not ir.pages:
        for i in range(page_count):
            w, h, rot = page_size(pdf_path, i)
            ir.pages.append(
                PageInfo(page=i + 1, width=w, height=h, rotation=rot, page_type=PageType.NATIVE_TEXT)
            )
    if not ir.blocks:
        ir.blocks.extend(_text_to_blocks(1, md))
        ir.warnings.append("docling_markdown_without_page_markers")
    return ir


def _convert_local(pdf_path: Path, out: Path, prof: ConvertProfile) -> DocumentIR:
    page_count = _page_count(str(pdf_path))
    pages = list(range(page_count))
    if prof.page_filter:
        wanted = {p - 1 for p in prof.page_filter}
        pages = [i for i in pages if i in wanted]

    # First pass: native text for watermark detection
    native_texts: Dict[int, str] = {}
    for i in pages:
        native_texts[i] = _extract_native_text(str(pdf_path), i)

    watermark_lines = [
        t
        for t, _c in repeated_line_candidates(
            [native_texts[i] for i in pages],
            fraction=prof.watermark_page_fraction,
        )
    ]

    ir = DocumentIR(
        schema_version=SCHEMA_VERSION,
        source_path=str(pdf_path),
        source_sha256=file_sha256(pdf_path),
        page_count=page_count,
        profile=prof.name,
        engine="local",
    )
    if watermark_lines:
        ir.warnings.append(f"watermark_candidates:{len(watermark_lines)}")

    block_idx_global = 0
    for i in pages:
        page_no = i + 1
        w, h, pdf_rot = page_size(pdf_path, i)
        native = strip_watermarks(native_texts[i], watermark_lines)
        img_count = _embedded_image_count(str(pdf_path), i)
        page_type, force_ocr = classify_page(
            native, embedded_image_count=img_count, profile=prof
        )

        rotation = pdf_rot
        page_image = None
        ocr_text = ""
        if force_ocr or page_type in {
            PageType.SCANNED,
            PageType.IMAGE_BASED,
            PageType.BROKEN_ENCODING,
        }:
            if not tesseract_available():
                raise OCRError("tesseract required for OCR pages but not installed")
            page_image = render_page(pdf_path, i, dpi=prof.dpi, rotation=0)
            try:
                osd = osd_image(page_image, dpi=prof.dpi)
                rotation = resolve_rotation(pdf_rot, osd)
                if rotation and rotation % 360 != 0:
                    page_image = render_page(pdf_path, i, dpi=prof.dpi, rotation=rotation)
            except OCRError as exc:
                ir.warnings.append(f"page {page_no} OSD failed: {exc}")
            ocr_text = ocr_image(
                page_image, lang=prof.ocr_lang, psm=prof.ocr_psm, dpi=prof.dpi
            )
            ocr_text = strip_watermarks(ocr_text, watermark_lines)
            page_png = out / "assets" / "pages" / f"page-{page_no:04d}.png"
            save_page_png(page_image, page_png, dpi=prof.dpi)
            text_for_blocks = ocr_text
        else:
            text_for_blocks = native

        info = PageInfo(
            page=page_no,
            width=w,
            height=h,
            rotation=rotation,
            page_type=page_type,
            text_layer_chars=len(native_texts[i]),
            ocr_chars=len(ocr_text),
            force_ocr=force_ocr,
        )
        ir.pages.append(info)

        page_blocks = _text_to_blocks(page_no, text_for_blocks, start_index=0)
        ir.blocks.extend(page_blocks)
        block_idx_global += len(page_blocks)

        # Tables (native pages via pdfplumber)
        if page_type in {PageType.NATIVE_TEXT, PageType.MIXED} and not force_ocr:
            try:
                tables = extract_tables_pdfplumber(str(pdf_path), i)
            except Exception as exc:  # noqa: BLE001
                ir.warnings.append(f"page {page_no} tables failed: {type(exc).__name__}: {exc}")
                tables = []
            for ti, table in enumerate(tables):
                ir.blocks.append(
                    Block(
                        block_id=stable_block_id(page_no, ti, "table"),
                        type=BlockType.TABLE,
                        page=page_no,
                        text="",
                        table=table,
                    )
                )

        # Formula candidates from text
        if prof.enable_formulas:
            for fi, raw in enumerate(detect_formula_candidates(text_for_blocks)[:5]):
                if raw.startswith("$"):
                    ir.blocks.append(
                        Block(
                            block_id=stable_block_id(page_no, fi, "formula"),
                            type=BlockType.FORMULA,
                            page=page_no,
                            formula=formula_from_latex(raw, confidence=0.4),
                        )
                    )
                else:
                    # keep failure explicit rather than empty success
                    asset = None
                    ir.blocks.append(
                        Block(
                            block_id=stable_block_id(page_no, fi, "formula"),
                            type=BlockType.FORMULA,
                            page=page_no,
                            formula=formula_failure(asset, f"unparsed_formula_hint:{raw[:40]}"),
                            text=raw,
                        )
                    )

        # Figures: if page rendered and image-based, store page render as figure candidate
        if prof.enable_figures and page_image is not None and page_type in {
            PageType.IMAGE_BASED,
            PageType.SCANNED,
            PageType.BROKEN_ENCODING,
        }:
            # Full-page figure only when almost no text blocks
            if len(ocr_text.strip()) < 80:
                fig_path = f"assets/figures/page-{page_no:04d}-full.png"
                save_page_png(page_image, out / fig_path, dpi=prof.dpi)
                ir.blocks.append(
                    Block(
                        block_id=stable_block_id(page_no, 0, "figure"),
                        type=BlockType.FIGURE,
                        page=page_no,
                        figure=figure_from_image(
                            fig_path,
                            ocr_text=ocr_text,
                            prompt=prof.figure_caption_prompt,
                        ),
                    )
                )

    return ir
