#!/usr/bin/env python3
"""
Standalone ISO/IEC 27001:2022 local audit verification script for ~/crypto_tools.

Checks:
  1. Git working tree cleanliness and HEAD commit (A.8.32)
  2. SHA-256 integrity of exported JSON audit bundles (A.5.28 / A.8.24)
  3. PII pattern scan across repository files (A.5.34 / A.8.11)
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PII_PATTERNS = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("PHONE", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("SIN_SSN_DASHED", re.compile(r"\b\d{3}-\d{2,3}-\d{3,4}\b")),
]

SKIP_DIRS = {".git", "__pycache__", "exports", ".venv", "venv"}
SCAN_EXTENSIONS = {".py", ".md", ".json", ".txt", ".yml", ".yaml", ".toml", ".sh"}


def check_git_cleanliness(repo_dir: Path) -> bool:
    print("[1/3] Checking Git working tree cleanliness (ISO 27001 A.8.32)...")
    try:
        inside = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if inside != "true":
            print("  FAIL: Not inside a Git working tree.")
            return False

        branch = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        head_short = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        status_out = subprocess.run(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        print(f"  Branch: {branch} | HEAD: {head_short}")
        if not status_out:
            print("  PASS: Working tree is clean.")
            return True

        lines = status_out.splitlines()
        print(f"  FAIL: Working tree has {len(lines)} uncommitted/untracked entry(ies):")
        for line in lines[:20]:
            print(f"    {line}")
        return False

    except subprocess.CalledProcessError as exc:
        print(f"  FAIL: Git command error: {exc.stderr.strip() or exc}")
        return False


def verify_single_bundle(bundle_path: Path) -> tuple[bool, str]:
    try:
        with bundle_path.open("r", encoding="utf-8") as f:
            bundle = json.load(f)
    except Exception as exc:
        return False, f"JSON parse error ({exc})"

    if not isinstance(bundle, dict) or "data" not in bundle:
        return False, "Missing 'data' payload"

    recorded = bundle.get("bundle_sha256")
    if not recorded:
        return False, "Missing 'bundle_sha256'"

    canonical = json.dumps(bundle["data"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    recomputed = hashlib.sha256(canonical).hexdigest()

    if recomputed == recorded:
        worker_id = bundle["data"].get("worker_id", "unknown")
        return True, f"VALID (worker={worker_id}, sha256={recorded[:12]}...)"
    return False, f"MISMATCH (recorded={recorded[:12]}..., recomputed={recomputed[:12]}...)"


def check_bundle_hashes(repo_dir: Path, exports_dir_name: str, require_bundles: bool) -> bool:
    print("[2/3] Verifying SHA-256 hashes on exported bundles (ISO 27001 A.5.28 / A.8.24)...")
    candidates: list[Path] = []

    exports_path = repo_dir / exports_dir_name
    if exports_path.exists() and exports_path.is_dir():
        candidates.extend(sorted(exports_path.glob("*.json")))

    top_bundle = repo_dir / "workers_advocate_audit_bundle.json"
    if top_bundle.exists() and top_bundle.is_file():
        candidates.append(top_bundle)

    # Deduplicate while preserving order
    seen = set()
    unique_bundles = []
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique_bundles.append(p)

    if not unique_bundles:
        msg = "No JSON audit bundles found in exports/ or root."
        if require_bundles:
            print(f"  FAIL: {msg}")
            return False
        print(f"  WARN: {msg}")
        return True

    all_valid = True
    for bp in unique_bundles:
        ok, detail = verify_single_bundle(bp)
        status = "PASS" if ok else "FAIL"
        rel = bp.relative_to(repo_dir) if bp.is_relative_to(repo_dir) else bp
        print(f"  {status}: {rel} -> {detail}")
        if not ok:
            all_valid = False

    return all_valid


def check_pii_patterns(repo_dir: Path) -> bool:
    print("[3/3] Scanning repository files for PII patterns (ISO 27001 A.5.34 / A.8.11)...")
    findings: list[tuple[str, str, int, str]] = []

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in sorted(files):
            file_path = Path(root) / fn
            if file_path.suffix.lower() not in SCAN_EXTENSIONS:
                continue
            if file_path.resolve() == Path(__file__).resolve():
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                findings.append((str(file_path), "READ_ERROR", 0, str(exc)))
                continue

            for line_no, line in enumerate(content.splitlines(), start=1):
                for label, pattern in PII_PATTERNS:
                    if pattern.search(line):
                        rel = file_path.relative_to(repo_dir)
                        snippet = line.strip()
                        if len(snippet) > 100:
                            snippet = snippet[:97] + "..."
                        findings.append((str(rel), label, line_no, snippet))

    if not findings:
        print("  PASS: 0 PII pattern matches detected.")
        return True

    print(f"  FAIL: {len(findings)} potential PII pattern match(es) found:")
    for rel, label, line_no, snippet in findings[:25]:
        print(f"    [{label}] {rel}:{line_no} -> {snippet}")
    if len(findings) > 25:
        print(f"    ... and {len(findings) - 25} more match(es).")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run ISO/IEC 27001:2022 local audit checks (Git cleanliness, bundle SHA-256, PII scan)."
    )
    parser.add_argument(
        "--repo-dir",
        default=".",
        help="Path to the repository directory (default: current directory)",
    )
    parser.add_argument(
        "--exports-dir",
        default="exports",
        help="Subdirectory containing exported JSON bundles (default: exports)",
    )
    parser.add_argument(
        "--require-bundles",
        action="store_true",
        help="Fail if no exported JSON bundles are found",
    )
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir).resolve()
    print(f"=== ISO 27001 Audit Check: {repo_dir} ===")

    git_ok = check_git_cleanliness(repo_dir)
    bundles_ok = check_bundle_hashes(repo_dir, args.exports_dir, args.require_bundles)
    pii_ok = check_pii_patterns(repo_dir)

    overall_ok = git_ok and bundles_ok and pii_ok
    print("=== Overall Result:", "COMPLIANT (PASS)" if overall_ok else "ACTION REQUIRED (FAIL)", "===")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
