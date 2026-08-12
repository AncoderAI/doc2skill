"""Benchmark runner over a corpus manifest."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from ..convert import convert_pdf
from ..optimize.net_guard import install_guard, is_active
from ..teachers import TEACHERS, convert as teacher_convert, probe
from . import score_against_truth, validate_bundle


def run_benchmark(corpus_manifest: Path, run_dir: Path) -> Dict[str, Any]:
    install_guard(allow_loopback=True)
    if not is_active():
        return {"ok": False, "error": "net_guard_inactive"}

    manifest = json.loads(Path(corpus_manifest).read_text(encoding="utf-8"))
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    docs = manifest.get("documents", [])
    results: Dict[str, Any] = {"documents": {}, "teachers": {}, "ok": True}
    t0 = time.perf_counter()

    for doc in docs:
        doc_id = doc["id"]
        pdf = _resolve_pdf(doc, manifest)
        truth = doc.get("truth", {})
        doc_out = run_dir / doc_id
        doc_out.mkdir(parents=True, exist_ok=True)

        # candidate engine
        bundle = doc_out / "candidate"
        report = convert_pdf(
            pdf,
            bundle,
            profile=doc.get("profile", "fast"),
            strict=False,
            profile_overrides={"page_filter": doc.get("pages")},
        )
        validation = validate_bundle(bundle)
        scores = score_against_truth(bundle, truth) if truth else report.get("scores", {})
        results["documents"][doc_id] = {
            "pdf": str(pdf),
            "quality": report,
            "validation": validation,
            "scores": scores,
        }
        if not validation["ok"] or not report.get("passed", False):
            # synthetic / private may still mark ok overall if configured
            if doc.get("require_pass", True):
                results["ok"] = False

        # teachers
        teacher_dir = doc_out / "teachers"
        teacher_dir.mkdir(exist_ok=True)
        results["teachers"][doc_id] = {}
        for tool in TEACHERS:
            tr = teacher_convert(tool, Path(pdf))
            (teacher_dir / f"{tool}.txt").write_text(tr.text, encoding="utf-8")
            (teacher_dir / f"{tool}.meta.json").write_text(
                json.dumps(tr.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            results["teachers"][doc_id][tool] = {
                "status": tr.status,
                "error": tr.error,
                "chars": len(tr.text),
                "sha256": tr.sha256,
            }

    results["elapsed_sec"] = time.perf_counter() - t0
    results["summary"] = {
        "ok": results["ok"],
        "documents": list(results["documents"].keys()),
        "elapsed_sec": results["elapsed_sec"],
        "probes": {t: probe(t) for t in TEACHERS},
    }
    (run_dir / "benchmark.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return results


def _resolve_pdf(doc: Dict[str, Any], manifest: Dict[str, Any]) -> Path:
    if doc.get("path"):
        p = Path(doc["path"])
        if not p.is_absolute():
            base = Path(manifest.get("base_dir", "."))
            p = base / p
        return p.resolve()
    corpus_dir = Path(manifest.get("corpus_dir") or "").expanduser()
    env = __import__("os").environ.get("PDF2MD_CORPUS_DIR")
    if env:
        corpus_dir = Path(env)
    name = doc.get("filename")
    if not name:
        raise ValueError(f"document {doc.get('id')} missing path/filename")
    return (corpus_dir / name).resolve()
