#!/usr/bin/env python3
"""
Workers Advocate: Give Back Workers' Power
Union Action & Representation Validation Module
"""

import sqlite3
import hashlib
from datetime import datetime, timezone

DB_NAME = "core_audit_sanitized.db"


def init_union_validation_tables():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS union_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            union_body TEXT NOT NULL,
            related_action_ref TEXT NOT NULL,
            action_status TEXT NOT NULL,
            days_elapsed INTEGER NOT NULL,
            notes TEXT NOT NULL,
            action_hash TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS union_validation_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            validated_at TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            validation_code TEXT NOT NULL,
            severity TEXT NOT NULL,
            finding TEXT NOT NULL,
            validation_hash TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def log_union_action(worker_id, union_body, related_action_ref, action_status, days_elapsed, notes):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    recorded_at = datetime.now(timezone.utc).isoformat()
    payload = (
        f"{recorded_at}|{worker_id}|{union_body}|{related_action_ref}|"
        f"{action_status}|{days_elapsed}|{notes}"
    )
    action_hash = _sha256(payload)

    cursor.execute("""
        INSERT INTO union_actions (
            recorded_at, worker_id, union_body, related_action_ref,
            action_status, days_elapsed, notes, action_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        recorded_at, worker_id, union_body, related_action_ref,
        action_status.upper(), int(days_elapsed), notes, action_hash
    ))

    conn.commit()
    conn.close()
    print(f"[+] Union action logged ({action_status.upper()}). SHA-256: {action_hash[:16]}...")


def validate_union_actions(worker_id, max_response_days=14):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, recorded_at, union_body, related_action_ref, action_status, days_elapsed, notes
        FROM union_actions
        WHERE worker_id = ?
        ORDER BY id ASC
    """, (worker_id,))
    actions = cursor.fetchall()

    cursor.execute("""
        SELECT id, rule_id, severity
        FROM inconsistency_flags
        WHERE worker_id = ?
        ORDER BY id ASC
    """, (worker_id,))
    inconsistencies = cursor.fetchall()

    validated_at = datetime.now(timezone.utc).isoformat()
    findings = []

    if inconsistencies and not actions:
        msg = (
            f"UNION_GAP_NO_RECORDED_ACTION: {len(inconsistencies)} workplace inconsistency flag(s) "
            f"exist for {worker_id}, but 0 union actions are logged in the audit ledger."
        )
        findings.append(("UNION_NO_RECORDED_ACTION", "HIGH", msg))

    for uid, rec_at, union_body, ref, status, days_elapsed, notes in actions:
        st = status.upper()

        if st in ("DELAYED", "NO_RESPONSE", "REFUSED_ESCALATION"):
            msg = (
                f"Union action #{uid} ({union_body}, ref '{ref}') recorded status '{st}' "
                f"after {days_elapsed} day(s): {notes}"
            )
            findings.append((f"UNION_{st}", "CRITICAL" if st == "REFUSED_ESCALATION" else "HIGH", msg))

        elif days_elapsed > max_response_days and st not in ("GRIEVANCE_FILED", "RESOLVED"):
            msg = (
                f"Union action #{uid} ({union_body}, ref '{ref}') took {days_elapsed} day(s) "
                f"(exceeds {max_response_days}-day benchmark) while status is '{st}': {notes}"
            )
            findings.append(("UNION_PROTRACTED_TIMELINE", "MEDIUM", msg))

    for code, severity, finding in findings:
        v_hash = _sha256(f"{validated_at}|{worker_id}|{code}|{severity}|{finding}")
        cursor.execute("""
            INSERT INTO union_validation_flags (
                validated_at, worker_id, validation_code, severity, finding, validation_hash
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (validated_at, worker_id, code, severity, finding, v_hash))

    conn.commit()
    conn.close()
    return findings


def run_union_demo():
    init_union_validation_tables()
    worker = "[Worker_002]"

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM union_actions WHERE worker_id = ?", (worker,))
    cursor.execute("DELETE FROM union_validation_flags WHERE worker_id = ?", (worker,))
    conn.commit()
    conn.close()

    action_id = log_union_action(
        worker_id=worker,
        union_body="[Union_Local_A]",
        related_action_ref="DISMISSAL_2021_05_12",
        action_status="DISMISSED",
        days_elapsed=21,
        notes=(
            "Sanitized [Worker_002] / [Union_Local_A]: dismissed 2021-05-12; union grievance dismissed 2021; "
            "ALRB DFR filed 2021 and dismissed 2024-06-30. Relied on employer material without independent "
            "investigation or witness check; claimed language/communication barrier; disputed in-person "
            "grievance review board defense narrative."
        ),
    )
    print(f"[+] Logged union action #{action_id} for {worker}")

    flags = validate_union_actions(worker, max_response_days=14)
    print(f"[+] Validation complete for {worker}. Flags raised: {len(flags)}")
    for idx, flag in enumerate(flags, 1):
        if isinstance(flag, dict):
            print(f"  Flag {idx} [{flag.get('severity')}] - {flag.get('validation_code')}: {flag.get('finding')}")
        else:
            print(f"  Flag {idx}: {flag}")


if __name__ == "__main__":
    run_union_demo()
