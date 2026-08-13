"""CLI: book-to-skill-pdf2md."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="book-to-skill-pdf2md",
        description="High-fidelity offline PDF → Markdown subsystem",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_doc = sub.add_parser("doctor", help="Check offline readiness")
    p_doc.add_argument("--json", action="store_true", help="Machine-readable JSON")

    p_conv = sub.add_parser("convert", help="Convert PDF to pdf2md bundle")
    p_conv.add_argument("input", type=str)
    p_conv.add_argument("--output", required=True, type=str)
    p_conv.add_argument(
        "--profile", choices=["auto", "fast", "accurate"], default="auto"
    )
    p_conv.add_argument("--strict", action="store_true")
    p_conv.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Optional 1-based page list, e.g. 1,2,5-8 (debug/sentinel)",
    )

    p_bench = sub.add_parser("benchmark", help="Run teacher + candidate benchmark")
    p_bench.add_argument(
        "pdfs",
        nargs="*",
        type=str,
        help="PDF files to benchmark; omit when using --corpus",
    )
    p_bench.add_argument(
        "--corpus",
        type=str,
        default=None,
        help="Corpus manifest, for pinned page lists and ground truth",
    )
    p_bench.add_argument("--run-dir", required=True, type=str)
    p_bench.add_argument(
        "--sample",
        type=int,
        default=6,
        help="Pages per PDF when passing files directly (0 = every page)",
    )
    p_bench.add_argument(
        "--profile",
        choices=["auto", "fast", "accurate"],
        default="accurate",
        help="Profile for generated corpus manifests (was hard-coded fast)",
    )
    p_bench.add_argument("--json", action="store_true", help="Machine-readable JSON")

    p_opt = sub.add_parser("optimize", help="Search profiles and optionally auto-commit")
    p_opt.add_argument("--corpus", required=True, type=str)
    p_opt.add_argument("--base-ref", required=True, type=str)
    p_opt.add_argument("--budget", type=int, default=8)
    p_opt.add_argument("--auto-commit", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        from .doctor import run_doctor

        report = run_doctor()
        if args.json:
            sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        else:
            print("ok:" if report["ok"] else "NOT OK:", report.get("issues"))
            for hint in report.get("hints") or []:
                print("hint:", hint)
            print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ok"] else 1

    if args.cmd == "convert":
        from .convert import convert_pdf

        overrides = {}
        if args.pages:
            overrides["page_filter"] = _parse_pages(args.pages)
        report = convert_pdf(
            args.input,
            args.output,
            profile=args.profile,
            strict=args.strict,
            profile_overrides=overrides or None,
        )
        print(json.dumps({"passed": report.get("passed"), "total_score": report.get("total_score"), "failures": report.get("failures")}, ensure_ascii=False))
        if args.strict and not report.get("passed"):
            return 2
        return 0

    if args.cmd == "benchmark":
        from .eval.benchmark import run_benchmark

        if bool(args.pdfs) == bool(args.corpus):
            print(
                "benchmark: pass PDF files or --corpus, not both and not neither",
                file=sys.stderr,
            )
            return 2

        run_dir = Path(args.run_dir)
        if args.corpus:
            corpus_path = Path(args.corpus)
            if not corpus_path.is_file():
                print(f"benchmark: corpus not found: {corpus_path}", file=sys.stderr)
                return 2
        else:
            missing = [p for p in args.pdfs if not Path(p).is_file()]
            if missing:
                for path in missing:
                    print(f"benchmark: no such PDF: {path}", file=sys.stderr)
                return 2
            corpus_path = _write_generated_corpus(
                args.pdfs, run_dir, args.sample, profile=args.profile
            )

        result = run_benchmark(corpus_path, run_dir)
        if args.json:
            print(json.dumps(result.get("summary", result), indent=2, ensure_ascii=False))
        else:
            _print_benchmark_summary(result, run_dir)
        return 0 if result.get("ok") else 1

    if args.cmd == "optimize":
        from .optimize.runner import run_optimize

        result = run_optimize(
            corpus=Path(args.corpus),
            base_ref=args.base_ref,
            budget=args.budget,
            auto_commit=args.auto_commit,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    return 1


def _page_count(pdf_path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


def _sample_pages(total: int, sample: int) -> list[int] | None:
    """Evenly spaced 1-based pages, always including the first and last."""
    if sample <= 0 or total <= sample:
        return None
    if sample == 1:
        return [1]
    step = (total - 1) / (sample - 1)
    return sorted({1 + round(i * step) for i in range(sample)})


def _write_generated_corpus(
    pdfs: list[str], run_dir: Path, sample: int, *, profile: str = "accurate"
) -> Path:
    """Build a manifest from bare PDF paths so callers need not hand-write one.

    It is written to disk rather than kept in memory so the run stays
    reproducible: the file records exactly which pages were measured.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    used_ids: set[str] = set()
    for path in pdfs:
        pdf_path = Path(path).resolve()
        doc_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in pdf_path.stem)[:48]
        candidate = doc_id or "doc"
        suffix = 2
        while candidate in used_ids:
            candidate = f"{doc_id}_{suffix}"
            suffix += 1
        used_ids.add(candidate)

        total = _page_count(pdf_path)
        entry = {
            "id": candidate,
            "path": str(pdf_path),
            "profile": profile,
            "require_pass": False,
        }
        pages = _sample_pages(total, sample) if total else None
        if pages:
            entry["pages"] = pages
        if total:
            entry["total_pages"] = total
        documents.append(entry)

    manifest_path = run_dir / "corpus.generated.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "1.0.0", "documents": documents}, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _block_counts(bundle_dir: Path) -> str:
    ir_path = bundle_dir / "document.ir.json"
    if not ir_path.is_file():
        return "-"
    try:
        blocks = json.loads(ir_path.read_text(encoding="utf-8")).get("blocks", [])
    except (OSError, ValueError):
        return "-"
    tally: dict[str, int] = {}
    for block in blocks:
        tally[block.get("type", "?")] = tally.get(block.get("type", "?"), 0) + 1
    if not tally:
        return "none"
    return " · ".join(f"{name} {count}" for name, count in sorted(tally.items()))


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _total_pages_by_id(run_dir: Path) -> dict[str, int]:
    """Page totals recorded by a generated manifest, for 'measured N of M'."""
    manifest_path = run_dir / "corpus.generated.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {
        doc["id"]: doc["total_pages"]
        for doc in manifest.get("documents", [])
        if doc.get("id") and doc.get("total_pages")
    }


def _print_benchmark_summary(result: dict, run_dir: Path) -> None:
    documents = result.get("documents", {})
    teachers = result.get("teachers", {})
    if not documents:
        print("benchmark: no documents were measured")
        return

    totals = _total_pages_by_id(run_dir)
    for doc_id, entry in documents.items():
        pdf_name = Path(entry.get("pdf", doc_id)).name
        scores = entry.get("scores") or {}
        quality = entry.get("quality") or {}
        counts = quality.get("counts") or {}
        bundle = run_dir / doc_id / "candidate"

        print(f"\n{pdf_name}")
        measured = counts.get("pages")
        if measured is not None:
            total_pages = totals.get(doc_id)
            of_total = f" of {total_pages}" if total_pages else ""
            print(f"  pages measured   {measured}{of_total}")
        total = scores.get("total")
        if total is not None:
            detail = " · ".join(
                f"{label} {scores[key]}"
                for key, label in (
                    ("text_ocr", "text"),
                    ("heading_order", "order"),
                    ("tables", "tables"),
                    ("figures", "figures"),
                    ("formulas", "formulas"),
                )
                if key in scores
            )
            print(f"  score            {total} / 100   ({detail})")
        print(f"  blocks           {_block_counts(bundle)}")

        tools = teachers.get(doc_id) or {}
        if tools:
            rendered = " · ".join(
                f"{name} {info['chars']}" if info.get("status") == "ok" else f"{name} {info.get('status')}"
                for name, info in tools.items()
            )
            print(f"  reference tools  {rendered}")
        print(f"  markdown         {_display_path(bundle / 'document.md')}")

    print(f"\nfull report: {_display_path(run_dir / 'benchmark.json')}")
    if not result.get("ok"):
        print("status: FAILED — see validation/quality sections in the full report")


def _parse_pages(spec: str) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            pages.update(range(lo, hi + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


if __name__ == "__main__":
    raise SystemExit(main())
