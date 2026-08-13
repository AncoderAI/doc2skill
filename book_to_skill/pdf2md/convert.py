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
from .handles import close_all, get_plumber_page, get_pypdf, page_count as cached_page_count
from .figures import (
    crop_and_save,
    detect_formula_lines,
    detect_raster_figures,
    detect_region_figures,
    detect_vector_figures,
    figure_from_image,
    formula_failure,
    formula_from_latex,
    image_fingerprint,
    is_solid_or_tiny,
    parse_figures_from_markdown,
    parse_formulas_from_markdown,
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
from .ocr import OCRError, ocr_image, ocr_image_words, osd_image, resolve_rotation, tesseract_available
from .optimize.net_guard import install_guard, is_active
from .profiles import ConvertProfile, default_ocr_lang_for_path, resolve_profile
from .quality import build_quality_report
from .render import page_size, render_page, save_page_png
from .tables import (
    extract_tables_from_ocr_words,
    extract_tables_img2table,
    extract_tables_pdfplumber,
)

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
    reader = get_pypdf(pdf_path)
    page = reader.pages[page_index]
    return page.extract_text() or ""


def _embedded_image_count(pdf_path: str, page_index: int) -> int:
    reader = get_pypdf(pdf_path)
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
    return cached_page_count(pdf_path)


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
    page_offset: int = 0,
) -> Dict[str, Any]:
    """Convert PDF to pdf2md bundle. Returns quality report dict.

    On hard-gate failure with ``strict=True``, raises ``SystemExit``-style via
    returning report with passed=False; caller CLI maps to nonzero exit.
    ``page_offset`` is added to every emitted page number (default 0).
    """
    t0 = time.perf_counter()
    if install_network_guard:
        install_guard(allow_loopback=True)

    pdf_path = Path(input_path).resolve()
    out = Path(output_dir).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(str(pdf_path))

    try:
        return _convert_pdf_body(
            pdf_path, out, profile, strict, profile_overrides, t0, page_offset
        )
    finally:
        close_all()


def _convert_pdf_body(
    pdf_path: Path,
    out: Path,
    profile: str,
    strict: bool,
    profile_overrides: Optional[Dict[str, Any]],
    t0: float,
    page_offset: int = 0,
) -> Dict[str, Any]:
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
            ir = _try_docling(pdf_path, out, prof, page_offset=page_offset)
            if ir is not None:
                engine_name = "docling"
    except Exception:  # noqa: BLE001 — record, fall back
        # Docling optional; never pretend success
        pass

    if ir is None:
        ir = _convert_local(pdf_path, out, prof, page_offset=page_offset)
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

    md_text = assemble_markdown(ir)
    _apply_round_trips(ir, md_text)
    # Re-assemble after round_trip annotations
    md_text = assemble_markdown(ir)
    (out / "document.md").write_text(md_text, encoding="utf-8")
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
    # Attach B-stage detection logs when present
    fig_drops = getattr(ir, "_figure_drop_log", None)
    formula_log = getattr(ir, "_formula_score_log", None)
    if fig_drops is not None:
        drop_counts: Dict[str, int] = {}
        for d in fig_drops:
            reason = d.get("dropped") or "unknown"
            drop_counts[reason] = drop_counts.get(reason, 0) + 1
        report["figure_drops"] = {"counts": drop_counts, "items": fig_drops}
    if formula_log is not None:
        report["formula_candidates"] = {
            "total": len(formula_log),
            "passed": sum(1 for x in formula_log if x.get("passed")),
            "items": formula_log,
        }
    (out / "quality-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if strict and not report.get("passed", False):
        report["strict_failed"] = True
    return report


def _apply_round_trips(ir: DocumentIR, md_text: str) -> None:
    """Set figure.round_trip and formula meta from markdown re-parse."""
    md_figs = parse_figures_from_markdown(md_text)
    ir_figs = [b for b in ir.blocks if b.type == BlockType.FIGURE and b.figure]
    if len(md_figs) != len(ir_figs):
        reason = f"count_mismatch:md={len(md_figs)}:ir={len(ir_figs)}"
        for b in ir_figs:
            b.figure.round_trip = "failed"
            b.meta["round_trip_reason"] = reason
    else:
        for b, mf in zip(ir_figs, md_figs):
            if mf["path"] != b.figure.asset_path:
                b.figure.round_trip = "failed"
                b.meta["round_trip_reason"] = (
                    f"path_mismatch:md={mf['path']}:ir={b.figure.asset_path}"
                )
            else:
                b.figure.round_trip = "ok"

    md_forms = parse_formulas_from_markdown(md_text)
    ir_forms = [b for b in ir.blocks if b.type == BlockType.FORMULA and b.formula]
    # Compare counts of failed + latex; order may interleave — check presence
    md_failed = [x for x in md_forms if x["kind"] == "failed"]
    md_ok = [x for x in md_forms if x["kind"] == "latex"]
    ir_failed = [b for b in ir_forms if b.formula and b.formula.failed]
    ir_ok = [b for b in ir_forms if b.formula and not b.formula.failed]
    if len(md_failed) != len(ir_failed) or len(md_ok) != len(ir_ok):
        for b in ir_forms:
            b.meta["round_trip"] = "failed"
            b.meta["round_trip_reason"] = (
                f"formula_count_mismatch:md_failed={len(md_failed)}:"
                f"ir_failed={len(ir_failed)}:md_ok={len(md_ok)}:ir_ok={len(ir_ok)}"
            )
    else:
        for b in ir_forms:
            if b.formula and b.formula.failed:
                # failure must remain visible in md
                if not any(
                    (b.formula.failure_reason or "") == (m.get("reason") or "")
                    for m in md_failed
                ):
                    b.meta["round_trip"] = "failed"
                    b.meta["round_trip_reason"] = "failed_reason_not_in_markdown"
                else:
                    b.meta["round_trip"] = "ok"
            else:
                b.meta["round_trip"] = "ok"


def _try_docling(
    pdf_path: Path, out: Path, prof: ConvertProfile, *, page_offset: int = 0
) -> Optional[DocumentIR]:
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
                page=i + 1 + page_offset,
                width=0,
                height=0,
                rotation=0,
                page_type=PageType.NATIVE_TEXT,
            )
            for i in range(page_count)
        ]
        ir.blocks.extend(_text_to_blocks(1 + page_offset, md))
    # Always also run local enrichment for tables on native pages is skipped when docling works
    # Ensure page infos exist
    if not ir.pages:
        for i in range(page_count):
            w, h, rot = page_size(pdf_path, i)
            ir.pages.append(
                PageInfo(
                    page=i + 1 + page_offset,
                    width=w,
                    height=h,
                    rotation=rot,
                    page_type=PageType.NATIVE_TEXT,
                )
            )
    if not ir.blocks:
        ir.blocks.extend(_text_to_blocks(1 + page_offset, md))
        ir.warnings.append("docling_markdown_without_page_markers")
    return ir


def _convert_local(
    pdf_path: Path, out: Path, prof: ConvertProfile, *, page_offset: int = 0
) -> DocumentIR:
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
    figure_drop_log: List[Dict[str, Any]] = []
    formula_score_log: List[Dict[str, Any]] = []
    fingerprint_pages: Dict[str, List[int]] = {}
    kept_by_fp: Dict[str, List[Block]] = {}

    for i in pages:
        page_no = i + 1 + page_offset
        w, h, pdf_rot = page_size(pdf_path, i)
        native = strip_watermarks(native_texts[i], watermark_lines)
        img_count = _embedded_image_count(str(pdf_path), i)
        page_type, force_ocr = classify_page(
            native, embedded_image_count=img_count, profile=prof
        )

        rotation = pdf_rot
        page_image = None
        ocr_text = ""
        word_boxes: List[Any] = []
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
            try:
                word_boxes = ocr_image_words(
                    page_image,
                    lang=prof.ocr_lang,
                    psm=prof.ocr_psm,
                    dpi=prof.dpi,
                    page_size_pts=(w, h),
                )
            except OCRError as exc:
                ir.warnings.append(f"page {page_no} word-OCR failed: {exc}")
                word_boxes = []
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
        table_bboxes: List[Any] = []
        if page_type in {PageType.NATIVE_TEXT, PageType.MIXED} and not force_ocr:
            try:
                tables = extract_tables_pdfplumber(str(pdf_path), i)
            except Exception as exc:  # noqa: BLE001
                ir.warnings.append(f"page {page_no} tables failed: {type(exc).__name__}: {exc}")
                tables = []
            for ti, table in enumerate(tables):
                if table.bbox:
                    table_bboxes.append(tuple(table.bbox))
                ir.blocks.append(
                    Block(
                        block_id=stable_block_id(page_no, ti, "table"),
                        type=BlockType.TABLE,
                        page=page_no,
                        text="",
                        table=table,
                        bbox=tuple(table.bbox) if table.bbox else None,
                    )
                )
        elif (
            getattr(prof, "enable_ocr_tables", False)
            and page_type
            in {
                PageType.SCANNED,
                PageType.IMAGE_BASED,
                PageType.BROKEN_ENCODING,
            }
        ):
            # Scanned pages: img2table (CV) first; word-box projection as fallback.
            # No layout ML on the product path.
            tables = []
            route = "img2table"
            if page_image is not None:
                try:
                    tables = extract_tables_img2table(
                        page_image, page_w=w, page_h=h, dpi=prof.dpi, borderless=True
                    )
                except Exception as exc:  # noqa: BLE001
                    ir.warnings.append(
                        f"page {page_no} img2table failed: {type(exc).__name__}: {exc}"
                    )
                    tables = []
            if not tables:
                route = "ocr_word_projection"
                try:
                    tables = extract_tables_from_ocr_words(
                        word_boxes, page_w=w, page_h=h
                    )
                except Exception as exc:  # noqa: BLE001
                    ir.warnings.append(
                        f"page {page_no} ocr_tables failed: {type(exc).__name__}: {exc}"
                    )
                    tables = []
            for ti, table in enumerate(tables):
                if table.bbox:
                    table_bboxes.append(tuple(table.bbox))
                ir.blocks.append(
                    Block(
                        block_id=stable_block_id(page_no, ti, "table"),
                        type=BlockType.TABLE,
                        page=page_no,
                        text=table.caption or "",
                        table=table,
                        bbox=tuple(table.bbox) if table.bbox else None,
                        meta={"route": route},
                    )
                )

        # ---- Formulas (math-layout features; not $...$ only) ----
        if prof.enable_formulas:
            line_items = _line_items_for_formulas(
                pdf_path=str(pdf_path),
                page_index=i,
                page_no=page_no,
                text_for_blocks=text_for_blocks,
                page_type=page_type,
                force_ocr=force_ocr,
                word_boxes=word_boxes,
                page_h=h,
            )
            scored = detect_formula_lines(line_items, page=page_no)
            fi = 0
            for cand in scored:
                formula_score_log.append(
                    {
                        "page": page_no,
                        "line": cand.line,
                        "features": cand.features,
                        "classes_hit": cand.classes_hit,
                        "score": cand.score,
                        "passed": cand.passed,
                        "threshold_rule": cand.threshold_rule,
                        "bbox": list(cand.bbox) if cand.bbox else None,
                    }
                )
                if not cand.passed:
                    continue
                # Ensure page image for crop on failure path
                if page_image is None and cand.bbox is not None:
                    page_image = render_page(pdf_path, i, dpi=prof.dpi, rotation=rotation)
                asset_rel = None
                latex_guess = _try_trivial_latex(cand.line)
                if latex_guess:
                    fblock = formula_from_latex(latex_guess, confidence=0.35)
                    fblock.bbox = cand.bbox
                else:
                    if page_image is not None and cand.bbox is not None:
                        asset_rel = f"assets/formulas/page-{page_no:04d}-{fi:04d}.png"
                        crop_and_save(
                            page_image,
                            cand.bbox,
                            (w, h),
                            out / asset_rel,
                            dpi=prof.dpi,
                        )
                        reason = "no_latex_converter"
                    else:
                        reason = "no_latex_converter_and_no_crop_bbox"
                    fblock = formula_failure(asset_rel, reason)
                    fblock.bbox = cand.bbox
                ir.blocks.append(
                    Block(
                        block_id=stable_block_id(page_no, fi, "formula"),
                        type=BlockType.FORMULA,
                        page=page_no,
                        text=cand.line,
                        bbox=cand.bbox,
                        formula=fblock,
                        meta={"route": "math_features", "classes_hit": cand.classes_hit},
                    )
                )
                fi += 1

        # ---- Figures: vector / raster / region (no white-paper full-page hack) ----
        if prof.enable_figures:
            fig_cands = []
            # vector + raster on native/mixed; region on scanned/image-based
            if page_type in {PageType.NATIVE_TEXT, PageType.MIXED} and not force_ocr:
                fig_cands.extend(
                    detect_vector_figures(
                        str(pdf_path), i, page_no, w, h, table_bboxes
                    )
                )
                fig_cands.extend(detect_raster_figures(str(pdf_path), i, page_no, w, h))
            if page_type in {
                PageType.SCANNED,
                PageType.IMAGE_BASED,
                PageType.BROKEN_ENCODING,
            } or force_ocr:
                # raster first (will usually full_page-drop SIEMENS scans)
                fig_cands.extend(detect_raster_figures(str(pdf_path), i, page_no, w, h))
                if page_image is None:
                    page_image = render_page(pdf_path, i, dpi=prof.dpi, rotation=rotation)
                if not word_boxes and page_image is not None and tesseract_available():
                    try:
                        word_boxes = ocr_image_words(
                            page_image,
                            lang=prof.ocr_lang,
                            psm=prof.ocr_psm,
                            dpi=prof.dpi,
                            page_size_pts=(w, h),
                        )
                    except OCRError as exc:
                        ir.warnings.append(f"page {page_no} region word-OCR failed: {exc}")
                fig_cands.extend(
                    detect_region_figures(
                        page_image,
                        page_no,
                        w,
                        h,
                        word_boxes,
                        dpi=prof.dpi,
                    )
                )

            # Need render for cropping kept candidates on native pages
            kept = [c for c in fig_cands if c.dropped is None]
            if kept and page_image is None:
                page_image = render_page(pdf_path, i, dpi=prof.dpi, rotation=rotation)

            fig_i = 0
            for cand in fig_cands:
                entry = {
                    "page": page_no,
                    "route": cand.route,
                    "bbox": list(cand.bbox),
                    "dropped": cand.dropped,
                    "extra": cand.extra,
                }
                if cand.dropped is not None:
                    figure_drop_log.append(entry)
                    continue

                assert page_image is not None
                # Crop + validate asset
                asset_rel = f"assets/figures/page-{page_no:04d}-{fig_i:04d}-{cand.route}.png"
                crop_and_save(
                    page_image, cand.bbox, (w, h), out / asset_rel, dpi=prof.dpi
                )
                from PIL import Image as _PILImage

                crop_img = _PILImage.open(out / asset_rel)
                bad = is_solid_or_tiny(crop_img)
                fp = image_fingerprint(crop_img)
                crop_img.close()
                if bad:
                    cand.dropped = bad
                    entry["dropped"] = bad
                    entry["fingerprint"] = fp
                    figure_drop_log.append(entry)
                    (out / asset_rel).unlink(missing_ok=True)
                    continue

                # repeated decoration bookkeeping (finalize after all pages)
                fingerprint_pages.setdefault(fp, []).append(page_no)
                caption = None
                # caption binding from nearby text lines
                caption = _caption_from_text(text_for_blocks, cand.bbox)

                fblock = figure_from_image(
                    asset_rel,
                    bbox=cand.bbox,
                    ocr_text="",
                    prompt=prof.figure_caption_prompt,
                    caption=caption,
                )
                block = Block(
                    block_id=stable_block_id(page_no, fig_i, "figure"),
                    type=BlockType.FIGURE,
                    page=page_no,
                    text=caption or "",
                    bbox=cand.bbox,
                    figure=fblock,
                    meta={"route": cand.route, "fingerprint": fp, **cand.extra},
                )
                kept_by_fp.setdefault(fp, []).append(block)
                ir.blocks.append(block)
                fig_i += 1

    # Drop repeated decorations (≥3 pages share fingerprint)
    from .figures import REPEATED_DECORATION_MIN_PAGES

    drop_fps = {
        fp
        for fp, pgs in fingerprint_pages.items()
        if len(set(pgs)) >= REPEATED_DECORATION_MIN_PAGES
    }
    if drop_fps:
        new_blocks: List[Block] = []
        for b in ir.blocks:
            if b.type != BlockType.FIGURE:
                new_blocks.append(b)
                continue
            fp = (b.meta or {}).get("fingerprint")
            if fp in drop_fps:
                figure_drop_log.append(
                    {
                        "page": b.page,
                        "route": (b.meta or {}).get("route"),
                        "bbox": list(b.bbox) if b.bbox else None,
                        "dropped": "repeated_decoration",
                        "fingerprint": fp,
                    }
                )
                if b.figure and b.figure.asset_path:
                    ap = out / b.figure.asset_path
                    if ap.is_file():
                        ap.unlink()
                continue
            new_blocks.append(b)
        ir.blocks = new_blocks

    ir.warnings.append(f"figure_dropped:{len(figure_drop_log)}")
    ir.warnings.append(
        f"formula_candidates:{sum(1 for x in formula_score_log if x.get('passed'))}"
        f"/{len(formula_score_log)}"
    )
    setattr(ir, "_figure_drop_log", figure_drop_log)
    setattr(ir, "_formula_score_log", formula_score_log)
    return ir


def _approx_line_bbox(line: str, words: List[Any], page_h: float):
    """Best-effort bbox: union of words whose text appears in the line."""
    if not words or not line:
        return None
    tokens = [t for t in line.split() if t]
    if not tokens:
        return None
    matched = []
    for w in words:
        wt = str(w.get("text") or "")
        if wt and wt in line:
            matched.append(w)
    if not matched:
        return None
    x0 = min(float(x["x0"]) for x in matched)
    x1 = max(float(x["x1"]) for x in matched)
    tops = [float(x.get("top") or 0) for x in matched]
    bottoms = [float(x.get("bottom") or x.get("top") or 0) for x in matched]
    top, bottom = min(tops), max(bottoms)
    y1 = page_h - top
    y0 = page_h - bottom
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _try_trivial_latex(line: str) -> Optional[str]:
    """Only accept already-LaTeX or simple $...$ spans; never invent LaTeX."""
    s = (line or "").strip()
    if not s:
        return None
    if s.startswith("$") and s.endswith("$") and len(s) > 2:
        return s
    if "\\" in s and any(tok in s for tok in ("\\frac", "\\sum", "\\lambda", "\\int")):
        return s
    return None


def _caption_from_text(text: str, bbox) -> Optional[str]:
    from .figures import _CAPTION_RE

    for ln in (text or "").splitlines():
        m = _CAPTION_RE.search(ln)
        if m:
            return m.group(0)
    return None


def _line_items_for_formulas(
    *,
    pdf_path: str,
    page_index: int,
    page_no: int,
    text_for_blocks: str,
    page_type: PageType,
    force_ocr: bool,
    word_boxes: List[Any],
    page_h: float,
):
    """Return [(line_text, bbox|None), ...] for formula scoring."""
    items = []
    if page_type in {PageType.NATIVE_TEXT, PageType.MIXED} and not force_ocr:
        try:
            page = get_plumber_page(pdf_path, page_index)
            if page is None:
                raise IndexError(page_index)
            # Prefer extract_text lines — same grain as anchors.formula_lines.
            # Word-top clustering splits λ headers from "=0.024×D" bodies.
            raw_text = page.extract_text() or ""
            words = page.extract_words() or []
            for ln in raw_text.splitlines():
                text = ln.strip()
                if not text:
                    continue
                bbox = _approx_line_bbox(text, words, page_h)
                items.append((text, bbox))
            if items:
                return items
        except Exception as exc:  # noqa: BLE001 — fall back to OCR/plain lines; record
            items.append(
                (f"[formula_line_extract_failed:{type(exc).__name__}:{exc}]", None)
            )
            return items
    # OCR / fallback: plain lines without bbox, or word-box grouping
    if word_boxes:
        lines_map2: Dict[int, List[Any]] = {}
        for bb, text in word_boxes:
            key = int(round((bb[1] + bb[3]) / 2.0))
            lines_map2.setdefault(key, []).append((bb, text))
        for key in sorted(lines_map2, reverse=True):
            ws = sorted(lines_map2[key], key=lambda x: x[0][0])
            text = " ".join(t for _, t in ws).strip()
            if not text:
                continue
            x0 = min(b[0] for b, _ in ws)
            y0 = min(b[1] for b, _ in ws)
            x1 = max(b[2] for b, _ in ws)
            y1 = max(b[3] for b, _ in ws)
            items.append((text, (x0, y0, x1, y1)))
        if items:
            return items
    for ln in (text_for_blocks or "").splitlines():
        s = ln.strip()
        if s:
            items.append((s, None))
    return items
