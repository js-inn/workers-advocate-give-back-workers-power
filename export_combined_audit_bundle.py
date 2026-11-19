#!/usr/bin/env python3
"""
Workers Advocate: Give Back Workers' Power
Combined Audit & ALRB DFR Simulation JSON Bundle Exporter
"""

import sqlite3
import hashlib
import json
from datetime import datetime, timezone

CORE_DB_NAME = "core_audit_sanitized.db"
SIM_DB_NAME = "alrb_dfr_sim.db"


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_query(db_path, query, params=()):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


def build_combined_bundle(worker_id, include_all_sim_scenarios=True):
    inconsistency_flags = _safe_query(
        CORE_DB_NAME,
        """
        SELECT id, scanned_at, worker_id, rule_id, severity, finding, evidence_hash
        FROM inconsistency_flags
        WHERE worker_id = ?
        ORDER BY id ASC
        """,
        (worker_id,),
    )

    union_actions = _safe_query(
        CORE_DB_NAME,
        """
        SELECT id, recorded_at, worker_id, union_body, related_action_ref,
               action_status, days_elapsed, notes, action_hash
        FROM union_actions
        WHERE worker_id = ?
        ORDER BY id ASC
        """,
        (worker_id,),
    )

    dfr_fairness_results = _safe_query(
        CORE_DB_NAME,
        """
        SELECT id, evaluated_at, worker_id, union_action_id, dfr_rule_id,
               verdict_status, severity, deterministic_reason, result_hash
        FROM dfr_fairness_results
        WHERE worker_id = ?
        ORDER BY id ASC
        """,
        (worker_id,),
    )

    alrb_sim_results = []
    if include_all_sim_scenarios:
        alrb_sim_results = _safe_query(
            SIM_DB_NAME,
            """
            SELECT id, simulated_at, scenario_name, union_dfr_deterministic_status,
                   board_response_check_status, triggered_rules_json, simulation_hash
            FROM alrb_dfr_sim_results
            ORDER BY id ASC
            """,
        )
        for item in alrb_sim_results:
            try:
                item["triggered_rules"] = json.loads(item.pop("triggered_rules_json"))
            except (ValueError, TypeError):
                item["triggered_rules"] = []

    bundle_body = {
        "worker_id": worker_id,
        "summary": {
            "inconsistency_flags_count": len(inconsistency_flags),
            "union_actions_count": len(union_actions),
            "dfr_evaluations_count": len(dfr_fairness_results),
            "alrb_simulations_count": len(alrb_sim_results),
        },
        "workplace_inconsistency_flags": inconsistency_flags,
        "union_actions": union_actions,
        "dfr_fairness_results": dfr_fairness_results,
        "alrb_dfr_simulations": alrb_sim_results,
    }

    canonical_body = json.dumps(bundle_body, sort_keys=True, ensure_ascii=False)
    bundle_hash = _sha256(canonical_body)

    return {
        "project": "Workers Advocate: Give Back Workers' Power",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "bundle_sha256": bundle_hash,
        "data": bundle_body,
    }


def export_bundle_to_file(worker_id, output_filename="workers_advocate_audit_bundle.json"):
    bundle = build_combined_bundle(worker_id)
    with open(output_filename, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)
    print(f"[+] Combined JSON bundle written to: {output_filename}")
    print(f"[+] Bundle SHA-256: {bundle['bundle_sha256']}")
    print(f"[+] Counts: {bundle['data']['summary']}")
    return output_filename


if __name__ == "__main__":
    export_bundle_to_file("[Worker_001]")
