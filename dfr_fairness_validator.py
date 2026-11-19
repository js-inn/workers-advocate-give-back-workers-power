#!/usr/bin/env python3
"""
Workers Advocate: Give Back Workers' Power
Deterministic Duty of Fair Representation (DFR) Fairness Validator
"""

import sqlite3
import hashlib
from datetime import datetime, timezone

DB_NAME = "core_audit_sanitized.db"

DEFAULT_DFR_RULES = {
    "max_response_days": 14,
    "max_dismissal_grievance_days": 10,
}


def init_dfr_tables():
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
        CREATE TABLE IF NOT EXISTS dfr_action_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            union_action_id INTEGER NOT NULL,
            worker_id TEXT NOT NULL,
            is_dismissal_related INTEGER NOT NULL,
            worker_requested_review INTEGER NOT NULL,
            grievance_filed INTEGER NOT NULL,
            written_rationale_provided INTEGER NOT NULL,
            evidence_reviewed INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dfr_fairness_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluated_at TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            union_action_id INTEGER NOT NULL,
            dfr_rule_id TEXT NOT NULL,
            verdict_status TEXT NOT NULL,
            severity TEXT NOT NULL,
            deterministic_reason TEXT NOT NULL,
            result_hash TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_dfr_metrics(
    union_action_id,
    worker_id,
    is_dismissal_related,
    worker_requested_review,
    grievance_filed,
    written_rationale_provided,
    evidence_reviewed,
):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO dfr_action_metrics (
            union_action_id, worker_id, is_dismissal_related,
            worker_requested_review, grievance_filed,
            written_rationale_provided, evidence_reviewed
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        int(union_action_id),
        worker_id,
        int(bool(is_dismissal_related)),
        int(bool(worker_requested_review)),
        int(bool(grievance_filed)),
        int(bool(written_rationale_provided)),
        int(bool(evidence_reviewed)),
    ))
    conn.commit()
    conn.close()


def evaluate_dfr_fairness_deterministic(worker_id, rules=None):
    cfg = {**DEFAULT_DFR_RULES, **(rules or {})}
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
        SELECT COUNT(*)
        FROM inconsistency_flags
        WHERE worker_id = ? AND severity IN ('CRITICAL', 'HIGH')
    """, (worker_id,))
    high_or_crit_flags = cursor.fetchone()[0]

    evaluated_at = datetime.now(timezone.utc).isoformat()
    outcomes = []

    for ua_id, _, union_body, ref, status, days_elapsed, notes in actions:
        status_u = status.upper()

        cursor.execute("""
            SELECT is_dismissal_related, worker_requested_review, grievance_filed,
                   written_rationale_provided, evidence_reviewed
            FROM dfr_action_metrics
            WHERE union_action_id = ? AND worker_id = ?
            ORDER BY id DESC LIMIT 1
        """, (ua_id, worker_id))
        metric_row = cursor.fetchone()

        if metric_row is None:
            is_dismissal, requested, filed, rationale, reviewed = (0, 1, 0, 0, 0)
        else:
            is_dismissal, requested, filed, rationale, reviewed = metric_row

        triggered_rules = []

        day_limit = cfg["max_dismissal_grievance_days"] if is_dismissal else cfg["max_response_days"]
        if (is_dismissal or high_or_crit_flags > 0) and not filed and days_elapsed > day_limit:
            triggered_rules.append((
                "DFR_01_NO_GRIEVANCE_PAST_WINDOW",
                "CRITICAL",
                f"Action #{ua_id} ({union_body}, ref '{ref}') elapsed {days_elapsed}d (>{day_limit}d) "
                f"with grievance_filed=0, is_dismissal={bool(is_dismissal)}, and high_or_crit_flags={high_or_crit_flags}."
            ))

        if status_u in ("DELAYED", "NO_RESPONSE", "REFUSED_ESCALATION") and (not rationale or not reviewed):
            triggered_rules.append((
                "DFR_02_UNEXPLAINED_OR_UNREVIEWED_STALL",
                "HIGH",
                f"Action #{ua_id} status='{status_u}' after {days_elapsed}d with "
                f"written_rationale_provided={bool(rationale)} and evidence_reviewed={bool(reviewed)}."
            ))

        if requested and status_u in ("NO_RESPONSE", "REFUSED_ESCALATION"):
            triggered_rules.append((
                "DFR_03_REQUESTED_SUPPORT_UNFULFILLED",
                "CRITICAL",
                f"Action #{ua_id} had worker_requested_review=1 while status='{status_u}' ({notes})."
            ))

        if not triggered_rules:
            verdict = "FAIR_BASELINE_MET"
            sev = "INFO"
            reason = (
                f"Action #{ua_id} ({union_body}, ref '{ref}') met deterministic baseline rules "
                f"(days_elapsed={days_elapsed}, grievance_filed={bool(filed)}, "
                f"rationale={bool(rationale)}, evidence_reviewed={bool(reviewed)})."
            )
            r_hash = _sha256(f"{evaluated_at}|{worker_id}|{ua_id}|DFR_BASELINE_OK|{verdict}|{reason}")
            cursor.execute("""
                INSERT INTO dfr_fairness_results (
                    evaluated_at, worker_id, union_action_id, dfr_rule_id,
                    verdict_status, severity, deterministic_reason, result_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (evaluated_at, worker_id, ua_id, "DFR_BASELINE_OK", verdict, sev, reason, r_hash))
            outcomes.append(("DFR_BASELINE_OK", verdict, sev, reason))
        else:
            for rule_id, sev, reason in triggered_rules:
                r_hash = _sha256(f"{evaluated_at}|{worker_id}|{ua_id}|{rule_id}|DFR_FLAGGED|{reason}")
                cursor.execute("""
                    INSERT INTO dfr_fairness_results (
                        evaluated_at, worker_id, union_action_id, dfr_rule_id,
                        verdict_status, severity, deterministic_reason, result_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (evaluated_at, worker_id, ua_id, rule_id, "DFR_FLAGGED", sev, reason, r_hash))
                outcomes.append((rule_id, "DFR_FLAGGED", sev, reason))

    conn.commit()
    conn.close()
    return outcomes


def run_dfr_demo():
    init_dfr_tables()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id FROM union_actions WHERE worker_id = ? ORDER BY id DESC LIMIT 1
    """, ("[Worker_001]",))
    row = cursor.fetchone()
    conn.close()

    if row:
        ua_id = row[0]
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("DELETE FROM dfr_action_metrics WHERE union_action_id = ?", (ua_id,))
        cur.execute("DELETE FROM dfr_fairness_results WHERE worker_id = ?", ("[Worker_001]",))
        conn.commit()
        conn.close()

        record_dfr_metrics(
            union_action_id=ua_id,
            worker_id="[Worker_001]",
            is_dismissal_related=1,
            worker_requested_review=1,
            grievance_filed=0,
            written_rationale_provided=0,
            evidence_reviewed=0,
        )

    results = evaluate_dfr_fairness_deterministic("[Worker_001]")
    print(f"[+] Deterministic DFR evaluation complete. Findings: {len(results)}\n")
    for rule_id, verdict, sev, reason in results:
        print(f"  [{sev}] {rule_id} -> {verdict}")
        print(f"    {reason}\n")


if __name__ == "__main__":
    run_dfr_demo()
