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
    p_bench.add_argument("--corpus", required=True, type=str)
    p_bench.add_argument("--run-dir", required=True, type=str)

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

        result = run_benchmark(Path(args.corpus), Path(args.run_dir))
        print(json.dumps(result.get("summary", result), indent=2, ensure_ascii=False))
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
