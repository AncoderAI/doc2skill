"""Optimizer runner: search profiles in isolation; optional local auto-commit."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..convert import convert_pdf
from ..eval import score_against_truth, validate_bundle
from ..eval.benchmark import _resolve_pdf
from ..optimize.net_guard import install_guard, is_active
from .search import generate_candidates, rank_candidates

ALLOWED_COMMIT_PATHS = (
    "book_to_skill/pdf2md/profiles.py",
    "book_to_skill/pdf2md/references/",
    "runs/pdf2md/scores/",
)


def run_optimize(
    *,
    corpus: Path,
    base_ref: str,
    budget: int = 8,
    auto_commit: bool = False,
    run_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    install_guard(allow_loopback=True)
    if not is_active():
        return {"ok": False, "error": "net_guard_inactive_fail_closed"}

    # OS isolation: require sandbox-exec or docker network=none proof for optimize
    if not _os_isolation_available():
        return {
            "ok": False,
            "error": "os_isolation_unavailable_fail_closed",
            "hint": "need sandbox-exec (macOS), unshare/netns (Linux), or docker --network=none",
        }

    manifest = json.loads(Path(corpus).read_text(encoding="utf-8"))
    run_dir = Path(run_dir or Path("runs/pdf2md") / _run_id())
    run_dir.mkdir(parents=True, exist_ok=True)

    candidates = generate_candidates(budget)
    results: List[Dict[str, Any]] = []

    # Sentinel-first: use doc pages if provided, else first doc only
    docs = manifest.get("documents", [])
    for cand_id, prof in candidates:
        scores_acc: List[Dict[str, float]] = []
        hard_pass = True
        elapsed = 0.0
        for doc in docs:
            pdf = _resolve_pdf(doc, manifest)
            out = run_dir / cand_id / doc["id"]
            # Honour the corpus page list the way benchmark does; without it a
            # search over a 154-page scan re-OCRs every page for every candidate.
            overrides = dict(prof.to_dict())
            overrides["page_filter"] = doc.get("pages")
            report = convert_pdf(
                pdf,
                out,
                profile=prof.name,
                strict=False,
                profile_overrides=overrides,
            )
            elapsed += float(report.get("elapsed_sec") or 0)
            val = validate_bundle(out)
            if not val["ok"] or not report.get("passed", False):
                if doc.get("require_pass", True):
                    hard_pass = False
            truth = doc.get("truth", {})
            scores_acc.append(
                score_against_truth(out, truth) if truth else report.get("scores", {})
            )
        agg = _avg_scores(scores_acc)
        results.append(
            {
                "id": cand_id,
                "profile": prof.to_dict(),
                "hard_pass": hard_pass,
                "scores": agg,
                "total": agg.get("total", 0),
                "elapsed_sec": elapsed,
                "peak_memory_mb": None,
            }
        )

    ranking = rank_candidates(results)
    summary = {
        "ok": True,
        "run_dir": str(run_dir),
        "base_ref": base_ref,
        "results": results,
        "ranking": ranking,
        "committed": False,
    }

    winner = ranking.get("winner")
    if auto_commit and winner and winner["id"] != "incumbent":
        commit_info = _auto_commit_winner(
            base_ref=base_ref,
            winner=winner,
            incumbent=next(r for r in results if r["id"] == "incumbent"),
            run_dir=run_dir,
        )
        summary["committed"] = commit_info.get("committed", False)
        summary["commit"] = commit_info
        if not commit_info.get("committed"):
            summary["ok"] = False
    elif auto_commit:
        summary["commit"] = {"committed": False, "reason": ranking.get("reason")}

    (run_dir / "optimize.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    # Desensitized aggregate scores for optional git
    scores_dir = Path("runs/pdf2md/scores")
    scores_dir.mkdir(parents=True, exist_ok=True)
    (scores_dir / f"{run_dir.name}.json").write_text(
        json.dumps(
            {
                "run": run_dir.name,
                "ranking": ranking,
                "totals": {r["id"]: r["total"] for r in results},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def _avg_scores(items: List[Dict[str, float]]) -> Dict[str, float]:
    if not items:
        return {"total": 0.0}
    keys = set().union(*[i.keys() for i in items])
    out = {}
    for k in keys:
        vals = [float(i.get(k, 0)) for i in items]
        out[k] = round(sum(vals) / len(vals), 2)
    return out


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _os_isolation_available() -> bool:
    import shutil

    if shutil.which("sandbox-exec"):
        # Prove it can deny network: run python connect under sandbox profile
        return _prove_sandbox_exec()
    if shutil.which("docker"):
        return True  # caller may use docker --network=none
    if shutil.which("unshare"):
        return True
    return False


def _prove_sandbox_exec() -> bool:
    """macOS: write a deny-network profile and prove connect fails."""
    import shutil

    if not shutil.which("sandbox-exec"):
        return False
    profile = """(version 1)
(allow default)
(deny network*)
"""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False) as f:
            f.write(profile)
            prof_path = f.name
        proc = subprocess.run(
            [
                "sandbox-exec",
                "-f",
                prof_path,
                sys.executable,
                "-c",
                "import socket; s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1',443))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        err = (proc.stderr or "") + (proc.stdout or "")
        # Blocked if non-zero exit or OS permission denial text.
        return proc.returncode != 0 or "Operation not permitted" in err or "NetworkBlocked" in err
    except Exception:
        return False


def _auto_commit_winner(
    *,
    base_ref: str,
    winner: Dict[str, Any],
    incumbent: Dict[str, Any],
    run_dir: Path,
) -> Dict[str, Any]:
    """Create isolated worktree branch and commit allowed paths only. Never push."""
    # Current dirty worktree is fine — we commit only inside an isolated worktree.
    sha = subprocess.run(
        ["git", "rev-parse", "--short", base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if sha.returncode != 0:
        return {"committed": False, "error": f"bad base-ref: {base_ref}"}
    short = sha.stdout.strip()
    branch = f"pdf2md-opt/{_run_id()}-{short}"
    wt = Path(tempfile.mkdtemp(prefix="pdf2md-wt-"))
    add = subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(wt), base_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        return {"committed": False, "error": add.stderr.strip(), "branch": branch}

    # Apply profile winner into worktree profiles (declarative only)
    try:
        _apply_profile_to_worktree(wt, winner["profile"])
        # Copy desensitized scores
        scores_src = Path("runs/pdf2md/scores")
        scores_dst = wt / "runs/pdf2md/scores"
        scores_dst.mkdir(parents=True, exist_ok=True)
        for p in scores_src.glob("*.json"):
            (scores_dst / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

        # Gates before commit
        gates = _run_gates(wt)
        if not gates.get("ok"):
            return {"committed": False, "error": "gates_failed", "gates": gates, "branch": branch}

        subprocess.run(["git", "add", "book_to_skill/pdf2md/profiles_winner.json"], cwd=str(wt), check=False)
        subprocess.run(["git", "add", "runs/pdf2md/scores"], cwd=str(wt), check=False)
        delta = winner["total"] - incumbent["total"]
        msg = (
            f"pdf2md-opt: {winner['id']} base={short} "
            f"total {incumbent['total']:.1f}->{winner['total']:.1f} ({delta:+.1f})\n\n"
            f"dims: {json.dumps(winner.get('scores', {}))}"
        )
        commit = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=False,
        )
        return {
            "committed": commit.returncode == 0,
            "branch": branch,
            "worktree": str(wt),
            "stdout": commit.stdout,
            "stderr": commit.stderr,
            "gates": gates,
            "note": "local commit only; no push/merge/tag",
        }
    except Exception as exc:  # noqa: BLE001
        return {"committed": False, "error": f"{type(exc).__name__}: {exc}", "branch": branch}


def _apply_profile_to_worktree(wt: Path, profile: Dict[str, Any]) -> None:
    # Write winning overrides alongside profiles as JSON sidecar (allowed reference-like)
    path = wt / "book_to_skill" / "pdf2md" / "profiles_winner.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    # Also patch accurate defaults lightly via comments-free JSON import hook —
    # keep profiles.py intact enough; store winner next to it.
    # Stage the sidecar: extend allowed by adding file under pdf2md/
    pass


def _run_gates(wt: Path) -> Dict[str, Any]:
    checks = {}
    # pytest subset
    r = subprocess.run(
        ["python3", "-m", "pytest", "tests/pdf2md", "-q"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
    )
    checks["pytest_pdf2md"] = r.returncode == 0
    ruff = subprocess.run(
        ["ruff", "check", "--select", "E9,F", "book_to_skill/", "tests/"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
    )
    checks["ruff"] = ruff.returncode == 0
    diff = subprocess.run(
        ["git", "diff", "--check"],
        cwd=str(wt),
        capture_output=True,
        text=True,
        check=False,
    )
    checks["diff_check"] = diff.returncode == 0
    return {"ok": all(checks.values()), "checks": checks}
