#!/usr/bin/env python3
"""
Workers Advocate: Give Back Workers' Power
Workplace Inconsistency & Due-Process Scanner (Sanitized Core)
"""

import sqlite3
import hashlib
import json
from datetime import datetime, timezone

DB_NAME = "core_audit_sanitized.db"


def init_inconsistency_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS worker_complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filed_at TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            complaint_category TEXT NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL,
            resolved_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS disciplinary_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_date TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            cited_infraction TEXT NOT NULL,
            workplace_baseline_frequency TEXT NOT NULL,
            peers_disciplined_for_same INTEGER NOT NULL,
            observed_at TEXT,
            notified_worker_at TEXT,
            notified_at_moment_observed INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hardship_and_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            description TEXT NOT NULL,
            request_status TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inconsistency_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            finding TEXT NOT NULL,
            evidence_hash TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def _hash_finding(scanned_at, worker_id, rule_id, finding):
    raw = f"{scanned_at}|{worker_id}|{rule_id}|{finding}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def scan_worker_case(worker_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    flags = []
    scanned_at = datetime.now(timezone.utc).isoformat()

    cursor.execute("""
        SELECT id, filed_at, complaint_category, summary, status
        FROM worker_complaints
        WHERE worker_id = ?
        ORDER BY filed_at ASC
    """, (worker_id,))
    complaints = cursor.fetchall()

    cursor.execute("""
        SELECT id, action_date, action_type, cited_infraction,
               workplace_baseline_frequency, peers_disciplined_for_same,
               observed_at, notified_worker_at, notified_at_moment_observed
        FROM disciplinary_actions
        WHERE worker_id = ?
        ORDER BY action_date ASC
    """, (worker_id,))
    actions = cursor.fetchall()

    cursor.execute("""
        SELECT id, recorded_at, event_type, description, request_status
        FROM hardship_and_requests
        WHERE worker_id = ?
        ORDER BY recorded_at ASC
    """, (worker_id,))
    hardships = cursor.fetchall()

    unresolved_complaints = [c for c in complaints if c[4].upper() in ("OPEN", "UNRESOLVED")]
    dismissals = [a for a in actions if a[2].upper() == "DISMISSAL"]

    if dismissals and unresolved_complaints:
        for d in dismissals:
            d_id, d_date, _, d_reason, _, _, _, _, _ = d
            prior_unresolved = [
                f"#{c[0]} ({c[2]} filed {c[1][:10]}: '{c[3]}')"
                for c in unresolved_complaints if c[1] <= d_date
            ]
            if prior_unresolved:
                finding = (
                    f"INCONSISTENCY #1 (Retaliation / Procedural Failure): Worker was issued a DISMISSAL "
                    f"on {d_date[:10]} (cited: '{d_reason}') while {len(prior_unresolved)} prior formal "
                    f"complaint(s) remained UNRESOLVED by administration: {'; '.join(prior_unresolved)}."
                )
                flags.append(("RULE_01_UNRESOLVED_COMPLAINT_DISMISSAL", "CRITICAL", finding))

    for a in actions:
        a_id, a_date, a_type, infraction, baseline, peer_count, _, _, _ = a
        if baseline.upper() == "COMMON_DAILY" and peer_count == 0:
            finding = (
                f"INCONSISTENCY #2 (Selective Enforcement): {a_type} issued on {a_date[:10]} for "
                f"'{infraction}', which is documented as a COMMON_DAILY workplace occurrence with "
                f"{peer_count} peers disciplined for the identical behavior. Indicates targeted discipline."
            )
            flags.append(("RULE_02_SELECTIVE_ENFORCEMENT", "HIGH", finding))

    for a in actions:
        a_id, a_date, a_type, infraction, _, _, obs_at, notif_at, immediate_flag = a
        if immediate_flag == 0:
            finding = (
                f"INCONSISTENCY #3 (Failure of Immediate Notice): {a_type} cites '{infraction}' "
                f"(observed: {obs_at or 'unspecified'}), but the worker was NOT notified at the moment "
                f"of observation (notified: {notif_at or 'delayed/retroactive'}). Deprives worker of "
                f"immediate correction and contemporaneous verification."
            )
            flags.append(("RULE_03_DELAYED_OBSERVATION_NOTICE", "HIGH", finding))

    active_hardships = [h for h in hardships if h[4].upper() in ("REFUSED", "UNRESOLVED", "OPEN")]
    for a in actions:
        a_id, a_date, a_type, infraction, _, _, _, _, _ = a
        infraction_l = infraction.lower()
        is_lateness_or_minor = any(k in infraction_l for k in ("late", "lateness", "tardy", "key"))
        if a_type.upper() == "DISMISSAL" and is_lateness_or_minor and active_hardships:
            h_summary = "; ".join(
                f"#{h[0]} [{h[2]} / {h[4]} on {h[1][:10]}: '{h[3]}']"
                for h in active_hardships
            )
            finding = (
                f"INCONSISTENCY #4 (Proportionality & Unresolved Hardship): DISMISSAL on {a_date[:10]} "
                f"cites '{infraction}', which may not establish just cause on its own while external "
                f"security/housing hardship and refused/unresolved requests were present: {h_summary}."
            )
            flags.append(("RULE_04_PROPORTIONALITY_HARDSHIP_CONTEXT", "CRITICAL", finding))

    for rule_id, severity, finding in flags:
        ev_hash = _hash_finding(scanned_at, worker_id, rule_id, finding)
        cursor.execute("""
            INSERT INTO inconsistency_flags (scanned_at, worker_id, rule_id, severity, finding, evidence_hash)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scanned_at, worker_id, rule_id, severity, finding, ev_hash))

    conn.commit()
    conn.close()
    return flags


def export_compliance_report(worker_id, output_path="audit_compliance_report.json"):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, scanned_at, rule_id, severity, finding, evidence_hash
        FROM inconsistency_flags
        WHERE worker_id = ?
        ORDER BY id ASC
    """, (worker_id,))
    rows = cursor.fetchall()
    conn.close()

    report = {
        "project": "Workers Advocate: Give Back Workers' Power",
        "worker_id": worker_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_flags": len(rows),
        "flags": [
            {
                "flag_id": r[0],
                "scanned_at": r[1],
                "rule_id": r[2],
                "severity": r[3],
                "finding": r[4],
                "evidence_hash": r[5],
            }
            for r in rows
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[+] Exported {len(rows)} flags to {output_path}")


def run_sanitized_demo():
    init_inconsistency_db()
    worker = "[Worker_002]"

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    # Clear prior rows for this worker so re-running stays clean
    cur.execute("DELETE FROM inconsistency_flags WHERE worker_id = ?", (worker,))
    cur.execute("DELETE FROM worker_complaints WHERE worker_id = ?", (worker,))
    cur.execute("DELETE FROM hardship_and_requests WHERE worker_id = ?", (worker,))
    cur.execute("DELETE FROM disciplinary_actions WHERE worker_id = ?", (worker,))

    if worker == "[Worker_002]":
        complaints_rows = [
            (
                "2021-03-01",
                worker,
                "Procedural & Workplace Conduct Dispute",
                "Sanitized pre-dismissal complaint [Worker_002]: contested PPE, key-return, and break allegations plus communication/representation concerns prior to 2021-05-12 dismissal; unresolved",
                "UNRESOLVED",
                None,
            ),
        ]
        hardship_rows = [
            (
                "2021-04-15",
                worker,
                "COMMUNICATION_AND_RECORD_DISPUTE",
                "Sanitized record [Worker_002]: requested fair witness verification and clear communication review prior to dismissal; refused",
                "REFUSED",
            ),
        ]
        disciplinary_rows = [
            (
                "2021-05-12",
                worker,
                "DISMISSAL",
                "Contested dismissal (2021-05-12): disputed PPE protocol, key-return, and 15-minute break allegations without independent witness verification",
                "COMMON_DAILY",
                0,
                "2021-05-12T09:00:00+00:00",
                "2021-05-12T09:00:00+00:00",
                1,
            ),
        ]
    else:
        complaints_rows = [
            ("2026-01-10", worker, "Workplace Harassment", "First sanitized complaint logged; unresolved", "UNRESOLVED", None),
        ]
        hardship_rows = [
            ("2026-02-12", worker, "SECURITY_DISRUPTION", "Sanitized external security/housing disruption; request refused", "REFUSED"),
        ]
        disciplinary_rows = [
            (
                "2026-03-01",
                worker,
                "DISMISSAL",
                "Cited attendance and cumulative workplace infractions",
                "COMMON_DAILY",
                0,
                "2026-03-01T09:00:00+00:00",
                "2026-03-01T09:00:00+00:00",
                1,
            ),
        ]

    # 1. Worker complaints (filed_at, worker_id, complaint_category, summary, status, resolved_at)
    cur.executemany(
        """
        INSERT INTO worker_complaints (filed_at, worker_id, complaint_category, summary, status, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        complaints_rows,
    )

    # 2. Hardship & requests (recorded_at, worker_id, event_type, description, request_status)
    cur.executemany(
        """
        INSERT INTO hardship_and_requests (recorded_at, worker_id, event_type, description, request_status)
        VALUES (?, ?, ?, ?, ?)
        """,
        hardship_rows,
    )

    # 3. Disciplinary actions
    cur.executemany(
        """
        INSERT INTO disciplinary_actions (
            action_date, worker_id, action_type, cited_infraction,
            workplace_baseline_frequency, peers_disciplined_for_same,
            observed_at, notified_worker_at, notified_at_moment_observed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        disciplinary_rows,
    )

    conn.commit()
    conn.close()

    flags = scan_worker_case(worker)
    print(f"[+] Scan complete for {worker}. Total Inconsistencies Flagged: {len(flags)}\n")
    for idx, f in enumerate(flags, 1):
        if isinstance(f, dict):
            sev = f.get("severity", "UNKNOWN")
            rule = f.get("rule_id", "UNKNOWN")
            desc = f.get("finding", "")
        else:
            # Handle tuple shapes safely: (rule_id, severity, finding, ...) or table row tuples
            if len(f) >= 6 and str(f[3]).startswith("RULE_"):
                rule, sev, desc = f[3], f[4], f[5]
            elif len(f) >= 3:
                rule, sev, desc = f[0], f[1], f[2]
            else:
                rule, sev, desc = "UNKNOWN", "UNKNOWN", str(f)
        print(f"Flag {idx} [{sev}] - {rule}:")
        print(f"  {desc}\n")

    export_compliance_report(worker, "audit_compliance_report.json")


if __name__ == "__main__":
    run_sanitized_demo()
