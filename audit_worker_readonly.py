#!/usr/bin/env python3
"""
Read-only audit inspector for a target worker_id across core_audit_sanitized.db
and alrb_dfr_sim.db. Opens databases in read-only mode (?mode=ro) and performs
zero write operations.
"""

import argparse
import json
import sqlite3
from pathlib import Path

CORE_DB = "core_audit_sanitized.db"
SIM_DB = "alrb_dfr_sim.db"


def _connect_ro(db_path: str):
    path = Path(db_path)
    if not path.exists():
        return None, f"Missing file: {db_path}"
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn, None
    except sqlite3.Error as exc:
        return None, f"Error opening {db_path} read-only: {exc}"


def _table_has_column(conn: sqlite3.Connection, table_name: str, col_name: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(c["name"] == col_name for c in cols)


def _list_tables(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name ASC"
    ).fetchall()
    return [r["name"] for r in rows]


def inspect_core_db(worker_id: str, db_path: str = CORE_DB):
    print(f"=== CORE DB (READ-ONLY): {db_path} | Target: {worker_id} ===")
    conn, err = _connect_ro(db_path)
    if err:
        print(f"  [!] {err}\n")
        return

    tables = _list_tables(conn)
    for table in tables:
        has_worker = _table_has_column(conn, table, "worker_id")
        if has_worker:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE worker_id = ? ORDER BY rowid ASC",
                (worker_id,),
            ).fetchall()
            scope_label = f"worker_id = {worker_id}"
        else:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid ASC").fetchall()
            scope_label = "no worker_id column (all rows)"

        print(f"\nTable: {table} ({scope_label}) -> {len(rows)} row(s)")
        if not rows:
            print("  (no matching rows)")
            continue

        for idx, r in enumerate(rows, 1):
            row_dict = dict(r)
            hash_fields = {
                k: v
                for k, v in row_dict.items()
                if "hash" in k.lower() or "uuid" in k.lower()
            }
            print(f"  [{idx}] {json.dumps(row_dict, ensure_ascii=False)}")
            if hash_fields:
                print(f"       Hashes: {json.dumps(hash_fields, ensure_ascii=False)}")

    conn.close()
    print()


def inspect_sim_db(db_path: str = SIM_DB):
    print(f"=== SIMULATION DB (READ-ONLY): {db_path} ===")
    conn, err = _connect_ro(db_path)
    if err:
        print(f"  [!] {err}\n")
        return

    tables = _list_tables(conn)
    for table in tables:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid ASC").fetchall()
        print(f"\nTable: {table} (scenario/benchmark table) -> {len(rows)} row(s)")
        if not rows:
            print("  (no rows)")
            continue

        for idx, r in enumerate(rows, 1):
            row_dict = dict(r)
            hash_fields = {
                k: v
                for k, v in row_dict.items()
                if "hash" in k.lower() or "uuid" in k.lower()
            }
            print(f"  [{idx}] {json.dumps(row_dict, ensure_ascii=False)}")
            if hash_fields:
                print(f"       Hashes: {json.dumps(hash_fields, ensure_ascii=False)}")

    conn.close()
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Read-only inspection of worker rows and hashes across audit SQLite databases."
    )
    parser.add_argument(
        "--worker-id",
        default="[Worker_002]",
        help="Worker identifier token to inspect (default: [Worker_002])",
    )
    parser.add_argument("--core-db", default=CORE_DB, help="Path to core SQLite DB")
    parser.add_argument("--sim-db", default=SIM_DB, help="Path to simulation SQLite DB")
    args = parser.parse_args()

    inspect_core_db(args.worker_id, args.core_db)
    inspect_sim_db(args.sim_db)


if __name__ == "__main__":
    main()
