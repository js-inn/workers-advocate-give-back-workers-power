#!/usr/bin/env python3
"""
Workers Advocate: Give Back Workers' Power
Neutral Deterministic Simulation Grounded in ALRB DFR Criteria
"""

import sqlite3
import hashlib
import json
from datetime import datetime, timezone

SIM_DB_NAME = "alrb_dfr_sim.db"


def init_sim_db():
    conn = sqlite3.connect(SIM_DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alrb_dfr_scenarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scenario_name TEXT NOT NULL,
            union_investigated_facts INTEGER NOT NULL,
            union_considered_merits_fairly INTEGER NOT NULL,
            union_free_of_bad_faith_or_bias INTEGER NOT NULL,
            union_communicated_timely INTEGER NOT NULL,
            worker_cooperated_and_timely INTEGER NOT NULL,
            board_reviewed_procedural_conduct INTEGER NOT NULL,
            board_addressed_key_evidence INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS alrb_dfr_sim_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            simulated_at TEXT NOT NULL,
            scenario_name TEXT NOT NULL,
            union_dfr_deterministic_status TEXT NOT NULL,
            board_response_check_status TEXT NOT NULL,
            triggered_rules_json TEXT NOT NULL,
            simulation_hash TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_alrb_deterministic_simulation(scenario):
    rules_triggered = []

    if not scenario["union_investigated_facts"] or not scenario["union_considered_merits_fairly"]:
        rules_triggered.append("ALRB_RULE_NON_ARBITRARY_INVESTIGATION_AND_REVIEW")

    if not scenario["union_free_of_bad_faith_or_bias"]:
        rules_triggered.append("ALRB_RULE_NO_BAD_FAITH_DISCRIMINATION_OR_HOSTILITY")

    if not scenario["union_communicated_timely"]:
        rules_triggered.append("ALRB_RULE_GENUINE_AND_TIMELY_HANDLING")

    if not scenario["worker_cooperated_and_timely"]:
        rules_triggered.append("ALRB_CONTEXT_EMPLOYEE_COOPERATION_AND_TIMELINESS")

    if not scenario["board_reviewed_procedural_conduct"] or not scenario["board_addressed_key_evidence"]:
        rules_triggered.append("ALRB_PROCESS_CHECK_PROCEDURAL_AND_EVIDENCE_REVIEW")

    union_issues = [
        r for r in rules_triggered
        if r.startswith("ALRB_RULE_") and r != "ALRB_CONTEXT_EMPLOYEE_COOPERATION_AND_TIMELINESS"
    ]
    union_status = "POTENTIAL_DFR_INDICATOR" if union_issues else "NO_DETERMINISTIC_DFR_BREACH_INDICATOR"

    board_issues = [r for r in rules_triggered if r.startswith("ALRB_PROCESS_CHECK_")]
    board_status = "PROCEDURAL_GAP_FLAGGED" if board_issues else "PROCEDURALLY_ALIGNED_WITH_CRITERIA"

    simulated_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({
        "simulated_at": simulated_at,
        "scenario": scenario,
        "union_status": union_status,
        "board_status": board_status,
        "rules_triggered": rules_triggered,
    }, sort_keys=True)
    sim_hash = _sha256(payload)

    conn = sqlite3.connect(SIM_DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO alrb_dfr_scenarios (
            scenario_name, union_investigated_facts, union_considered_merits_fairly,
            union_free_of_bad_faith_or_bias, union_communicated_timely,
            worker_cooperated_and_timely, board_reviewed_procedural_conduct,
            board_addressed_key_evidence
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        scenario["scenario_name"],
        int(bool(scenario["union_investigated_facts"])),
        int(bool(scenario["union_considered_merits_fairly"])),
        int(bool(scenario["union_free_of_bad_faith_or_bias"])),
        int(bool(scenario["union_communicated_timely"])),
        int(bool(scenario["worker_cooperated_and_timely"])),
        int(bool(scenario["board_reviewed_procedural_conduct"])),
        int(bool(scenario["board_addressed_key_evidence"])),
    ))

    cursor.execute("""
        INSERT INTO alrb_dfr_sim_results (
            simulated_at, scenario_name, union_dfr_deterministic_status,
            board_response_check_status, triggered_rules_json, simulation_hash
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        simulated_at,
        scenario["scenario_name"],
        union_status,
        board_status,
        json.dumps(rules_triggered),
        sim_hash,
    ))

    conn.commit()
    conn.close()

    return {
        "scenario_name": scenario["scenario_name"],
        "union_dfr_deterministic_status": union_status,
        "board_response_check_status": board_status,
        "triggered_rules": rules_triggered,
        "simulation_hash": sim_hash,
    }


def main():
    init_sim_db()
    conn = sqlite3.connect(SIM_DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM alrb_dfr_scenarios")
    cursor.execute("DELETE FROM alrb_dfr_sim_results")
    conn.commit()
    conn.close()

    test_cases = [
        {
            "scenario_name": "Case_A_Superficial_Union_And_Board_Review",
            "union_investigated_facts": 0,
            "union_considered_merits_fairly": 0,
            "union_free_of_bad_faith_or_bias": 1,
            "union_communicated_timely": 0,
            "worker_cooperated_and_timely": 1,
            "board_reviewed_procedural_conduct": 0,
            "board_addressed_key_evidence": 0,
        },
        {
            "scenario_name": "Case_B_Reasoned_Union_Decision_And_Procedural_Board_Review",
            "union_investigated_facts": 1,
            "union_considered_merits_fairly": 1,
            "union_free_of_bad_faith_or_bias": 1,
            "union_communicated_timely": 1,
            "worker_cooperated_and_timely": 1,
            "board_reviewed_procedural_conduct": 1,
            "board_addressed_key_evidence": 1,
        },
    ]

    for case in test_cases:
        out = run_alrb_deterministic_simulation(case)
        print(f"[+] Scenario: {out['scenario_name']}")
        print(f"    Union Status : {out['union_dfr_deterministic_status']}")
        print(f"    Board Check  : {out['board_response_check_status']}")
        print(f"    Rules        : {out['triggered_rules']}")
        print(f"    SHA-256      : {out['simulation_hash'][:24]}...\n")


if __name__ == "__main__":
    main()
