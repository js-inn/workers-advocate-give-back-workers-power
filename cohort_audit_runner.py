#!/usr/bin/env python3
"""
Multi-worker cohort runner: runs read-only inspection and exports per-worker
SHA-256-sealed JSON audit bundles with immediate integrity verification.
"""

import argparse
import hashlib
import json
from pathlib import Path

from audit_worker_readonly import CORE_DB, SIM_DB, inspect_core_db, inspect_sim_db
from export_combined_audit_bundle import export_bundle_to_file


def verify_bundle_file(bundle_path: Path) -> bool:
    with bundle_path.open("r", encoding="utf-8") as f:
        bundle = json.load(f)
    recomputed = hashlib.sha256(
        json.dumps(bundle["data"], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    recorded = bundle.get("bundle_sha256")
    matched = recomputed == recorded
    print(f"  Verify {bundle_path.name}: {'VALID (MATCH)' if matched else 'MISMATCH'} ({recorded})")
    return matched


def run_cohort(worker_ids, output_dir: str, core_db: str = CORE_DB, sim_db: str = SIM_DB, skip_readonly: bool = False):
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if not skip_readonly:
        inspect_sim_db(sim_db)

    all_ok = True
    for wid in worker_ids:
        safe_slug = wid.strip("[]").replace(" ", "_")
        target_file = out_path / f"audit_bundle_{safe_slug}.json"
        print(f"=== Cohort Worker: {wid} ===")
        if not skip_readonly:
            inspect_core_db(wid, core_db)
        export_bundle_to_file(wid, str(target_file))
        if not verify_bundle_file(target_file):
            all_ok = False
        print()

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Run read-only audit and export bundles for a worker cohort.")
    parser.add_argument(
        "--workers",
        nargs="+",
        default=["[Worker_001]", "[Worker_002]"],
        help="List of anonymized worker IDs (default: [Worker_001] [Worker_002])",
    )
    parser.add_argument("--output-dir", default="exports", help="Directory for per-worker JSON bundles")
    parser.add_argument("--core-db", default=CORE_DB, help="Path to core SQLite DB")
    parser.add_argument("--sim-db", default=SIM_DB, help="Path to simulation SQLite DB")
    parser.add_argument("--skip-readonly", action="store_true", help="Skip printing full read-only table dumps")
    args = parser.parse_args()

    ok = run_cohort(
        worker_ids=args.workers,
        output_dir=args.output_dir,
        core_db=args.core_db,
        sim_db=args.sim_db,
        skip_readonly=args.skip_readonly,
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
