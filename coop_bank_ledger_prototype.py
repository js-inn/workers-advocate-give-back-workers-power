#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS buckets (
    bucket_code TEXT PRIMARY KEY,
    bucket_name TEXT NOT NULL,
    target_share REAL NOT NULL,
    balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    member_token TEXT PRIMARY KEY,
    standing_state TEXT NOT NULL CHECK (
        standing_state IN (
            'ACTIVE_STANDING', 'GRACE_TRANSITION',
            'PAUSED_REINSTATABLE', 'GOOD_STANDING_INACTIVE'
        )
    ),
    tenure_months INTEGER NOT NULL DEFAULT 0 CHECK (tenure_months >= 0),
    dues_paid_count INTEGER NOT NULL DEFAULT 0 CHECK (dues_paid_count >= 0),
    grace_days_remaining INTEGER NOT NULL DEFAULT 0 CHECK (grace_days_remaining >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loans (
    loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_token TEXT NOT NULL REFERENCES members(member_token),
    loan_type TEXT NOT NULL CHECK (loan_type IN ('BRIDGE_LOAN', 'JOB_SEARCH_LOAN')),
    loan_state TEXT NOT NULL CHECK (
        loan_state IN (
            'DRAFT_REQUEST', 'ELIGIBILITY_CHECK', 'APPROVED', 'DISBURSED',
            'DECLINED', 'GRACE_REPAYMENT', 'ACTIVE_REPAYMENT',
            'HARDSHIP_MODIFIED', 'CLOSED_PAID', 'WRITTEN_OFF_RESERVE'
        )
    ),
    principal_cents INTEGER NOT NULL CHECK (principal_cents > 0),
    remaining_cents INTEGER NOT NULL CHECK (remaining_cents >= 0),
    grace_days INTEGER NOT NULL DEFAULT 45 CHECK (grace_days >= 0),
    decision_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_milestones (
    milestone_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_token TEXT NOT NULL,
    member_token TEXT NOT NULL REFERENCES members(member_token),
    milestone_stage TEXT NOT NULL CHECK (
        milestone_stage IN (
            'INTAKE_LOGGED', 'RECORD_AUDIT_COMPLETE',
            'RESOLUTION_OR_APPEAL_OUTCOME', 'LESSON_LEARNED_EXTRACTED',
            'JOB_SEARCH_TRACK_ACTIVE'
        )
    ),
    outcome_code TEXT NOT NULL,
    rule_codes_json TEXT NOT NULL,
    lesson_note TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    rule_code TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    bucket_code TEXT,
    delta_cents INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);
"""

DEFAULT_BUCKETS = [
    ("BUCKET_LENDING_POOL", "Cooperative Lending & Empowerment Pool", 0.55),
    ("BUCKET_LEGAL_DEFENSE", "Community Legal Defense & Advocacy Pool", 0.25),
    ("BUCKET_HARDSHIP_RESERVE", "Default, Hardship & Operations Reserve", 0.20),
]

ALLOWED_STANDING_TRANSITIONS = {
    "ACTIVE_STANDING": {"GRACE_TRANSITION", "PAUSED_REINSTATABLE", "GOOD_STANDING_INACTIVE"},
    "GRACE_TRANSITION": {"ACTIVE_STANDING", "PAUSED_REINSTATABLE", "GOOD_STANDING_INACTIVE"},
    "PAUSED_REINSTATABLE": {"ACTIVE_STANDING", "GRACE_TRANSITION", "GOOD_STANDING_INACTIVE"},
    "GOOD_STANDING_INACTIVE": {"ACTIVE_STANDING"},
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_event_hash(
    prev_hash: str, occurred_at: str, rule_code: str, entity_type: str,
    entity_id: str, from_state: Optional[str], to_state: Optional[str],
    bucket_code: Optional[str], delta_cents: int, payload_json: str,
) -> str:
    canonical = json.dumps(
        {
            "prev_hash": prev_hash, "occurred_at": occurred_at, "rule_code": rule_code,
            "entity_type": entity_type, "entity_id": entity_id, "from_state": from_state,
            "to_state": to_state, "bucket_code": bucket_code, "delta_cents": delta_cents,
            "payload": json.loads(payload_json),
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CoopBankLedger:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)
            now = utc_now_iso()
            for code, name, share in DEFAULT_BUCKETS:
                self.conn.execute(
                    "INSERT OR IGNORE INTO buckets VALUES (?, ?, ?, 0, ?)",
                    (code, name, share, now),
                )

    def _latest_hash(self) -> str:
        row = self.conn.execute("SELECT event_hash FROM ledger_events ORDER BY event_id DESC LIMIT 1").fetchone()
        return row["event_hash"] if row else "GENESIS_" + "0" * 56

    def _append_event(
        self, rule_code: str, entity_type: str, entity_id: str,
        from_state: Optional[str], to_state: Optional[str], bucket_code: Optional[str],
        delta_cents: int, payload: Dict[str, Any],
    ) -> str:
        now = utc_now_iso()
        prev_hash = self._latest_hash()
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        event_hash = compute_event_hash(
            prev_hash, now, rule_code, entity_type, entity_id,
            from_state, to_state, bucket_code, delta_cents, payload_json,
        )
        self.conn.execute(
            """
            INSERT INTO ledger_events (
                prev_hash, event_hash, occurred_at, rule_code, entity_type, entity_id,
                from_state, to_state, bucket_code, delta_cents, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prev_hash, event_hash, now, rule_code, entity_type, entity_id,
                from_state, to_state, bucket_code, delta_cents, payload_json,
            ),
        )
        return event_hash

    def _adjust_bucket(self, bucket_code: str, delta_cents: int, rule_code: str, ref_id: str, reason: str) -> None:
        row = self.conn.execute("SELECT balance_cents FROM buckets WHERE bucket_code = ?", (bucket_code,)).fetchone()
        if not row:
            raise ValueError(f"Unknown bucket: {bucket_code}")
        new_balance = row["balance_cents"] + delta_cents
        if new_balance < 0:
            raise ValueError(f"Insufficient funds in {bucket_code}")
        self.conn.execute(
            "UPDATE buckets SET balance_cents = ?, updated_at = ? WHERE bucket_code = ?",
            (new_balance, utc_now_iso(), bucket_code),
        )
        self._append_event(
            rule_code, "BUCKET", bucket_code, str(row["balance_cents"]), str(new_balance),
            bucket_code, delta_cents, {"ref_id": ref_id, "reason": reason},
        )

    def enroll_member(self, member_token: str, tenure_months: int = 0) -> None:
        assert member_token.startswith("[Member_")
        now = utc_now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO members VALUES (?, 'ACTIVE_STANDING', ?, 0, 0, ?, ?)",
                (member_token, tenure_months, now, now),
            )
            self._append_event("MEM_02_TIER_SCALING", "MEMBER", member_token, None, "ACTIVE_STANDING", None, 0, {"tenure_months": tenure_months})

    def post_dues(self, member_token: str, amount_cents: int, advance_tenure_months: int = 0) -> Dict[str, int]:
        assert amount_cents > 0
        lending = int(round(amount_cents * 0.55))
        legal = int(round(amount_cents * 0.25))
        reserve = amount_cents - lending - legal
        with self.conn:
            m = self.conn.execute("SELECT * FROM members WHERE member_token = ?", (member_token,)).fetchone()
            if not m:
                raise ValueError(f"Unknown member: {member_token}")
            new_tenure = m["tenure_months"] + advance_tenure_months
            new_paid = m["dues_paid_count"] + 1
            self.conn.execute(
                "UPDATE members SET tenure_months = ?, dues_paid_count = ?, updated_at = ? WHERE member_token = ?",
                (new_tenure, new_paid, utc_now_iso(), member_token),
            )
            split = {"BUCKET_LENDING_POOL": lending, "BUCKET_LEGAL_DEFENSE": legal, "BUCKET_HARDSHIP_RESERVE": reserve}
            self._append_event("DUES_01_ATOMIC_SPLIT", "MEMBER", member_token, m["standing_state"], m["standing_state"], None, amount_cents, {"dues_paid_count": new_paid, "split": split})
            self._adjust_bucket("BUCKET_LENDING_POOL", lending, "DUES_01_ATOMIC_SPLIT", member_token, "55% split")
            self._adjust_bucket("BUCKET_LEGAL_DEFENSE", legal, "DUES_01_ATOMIC_SPLIT", member_token, "25% split")
            self._adjust_bucket("BUCKET_HARDSHIP_RESERVE", reserve, "DUES_01_ATOMIC_SPLIT", member_token, "20% split")
        return split

    def transition_standing(self, member_token: str, new_state: str, reason: str, grace_days: int = 60, from_lost_appeal: bool = False) -> None:
        with self.conn:
            m = self.conn.execute("SELECT * FROM members WHERE member_token = ?", (member_token,)).fetchone()
            if not m:
                raise ValueError(f"Unknown member: {member_token}")
            old_state = m["standing_state"]
            if new_state not in ALLOWED_STANDING_TRANSITIONS[old_state]:
                raise ValueError(f"Invalid transition: {old_state} -> {new_state}")
            eff_state = "GRACE_TRANSITION" if (from_lost_appeal and new_state == "PAUSED_REINSTATABLE") else new_state
            eff_grace = grace_days if eff_state == "GRACE_TRANSITION" else 0
            self.conn.execute(
                "UPDATE members SET standing_state = ?, grace_days_remaining = ?, updated_at = ? WHERE member_token = ?",
                (eff_state, eff_grace, utc_now_iso(), member_token),
            )
            self._append_event("MEM_01_NO_APPEAL_PENALTY", "MEMBER", member_token, old_state, eff_state, None, 0, {"reason": reason, "from_lost_appeal": from_lost_appeal, "grace_days_remaining": eff_grace})

    def record_case_milestone(self, case_token: str, member_token: str, milestone_stage: str, outcome_code: str, rule_codes: List[str], lesson_note: str) -> int:
        assert case_token.startswith("[Case_")
        now = utc_now_iso()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO case_milestones (case_token, member_token, milestone_stage, outcome_code, rule_codes_json, lesson_note, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (case_token, member_token, milestone_stage, outcome_code, json.dumps(rule_codes, ensure_ascii=False), lesson_note, now),
            )
            mid = int(cur.lastrowid)
            self._append_event("PRIV_01_TOKENIZED_IDS_ONLY", "CASE_MILESTONE", f"{case_token}#{mid}", None, milestone_stage, None, 0, {"member_token": member_token, "outcome_code": outcome_code, "rule_codes": rule_codes, "lesson_note": lesson_note})
            return mid

def _loan_methods(cls: Any) -> Any:
    def create_and_process_loan(self: CoopBankLedger, member_token: str, loan_type: str, principal_cents: int, grace_days: int = 45) -> int:
        assert loan_type in ("BRIDGE_LOAN", "JOB_SEARCH_LOAN") and principal_cents > 0
        now = utc_now_iso()
        with self.conn:
            m = self.conn.execute("SELECT * FROM members WHERE member_token = ?", (member_token,)).fetchone()
            pool = self.conn.execute("SELECT balance_cents FROM buckets WHERE bucket_code = 'BUCKET_LENDING_POOL'").fetchone()
            cap = 150000 if m["tenure_months"] >= 3 else 50000
            max_pool = int(pool["balance_cents"] * 0.80)
            ok = m["standing_state"] in ("ACTIVE_STANDING", "GRACE_TRANSITION") and principal_cents <= cap and principal_cents <= max_pool
            cur = self.conn.execute(
                "INSERT INTO loans (member_token, loan_type, loan_state, principal_cents, remaining_cents, grace_days, decision_reason, created_at, updated_at) VALUES (?, ?, 'DRAFT_REQUEST', ?, ?, ?, 'Pending', ?, ?)",
                (member_token, loan_type, principal_cents, principal_cents, grace_days, now, now),
            )
            lid = int(cur.lastrowid)
            self._append_event("LOAN_01_MEMBERSHIP_QUALIFIES", "LOAN", str(lid), None, "DRAFT_REQUEST", None, 0, {"principal_cents": principal_cents})
            next_s = "APPROVED" if ok else "DECLINED"
            reason = f"{next_s} under {m['standing_state']} (cap={cap}c, max_pool={max_pool}c)"
            self.conn.execute("UPDATE loans SET loan_state = ?, decision_reason = ?, updated_at = ? WHERE loan_id = ?", (next_s, reason, utc_now_iso(), lid))
            self._append_event("LOAN_02_LIQUIDITY_AND_CAP", "LOAN", str(lid), "DRAFT_REQUEST", next_s, None, 0, {"reason": reason})
            if not ok:
                return lid
            self._adjust_bucket("BUCKET_LENDING_POOL", -principal_cents, "LOAN_02_LIQUIDITY_AND_CAP", f"LOAN_{lid}", "Disbursement")
            self.conn.execute("UPDATE loans SET loan_state = 'GRACE_REPAYMENT', updated_at = ? WHERE loan_id = ?", (utc_now_iso(), lid))
            self._append_event("LOAN_03_GRACE_RUNWAY", "LOAN", str(lid), "APPROVED", "GRACE_REPAYMENT", "BUCKET_LENDING_POOL", -principal_cents, {"grace_days": grace_days})
            return lid

    def modify_loan_hardship(self: CoopBankLedger, loan_id: int, extra_grace_days: int = 30, note: str = "Hardship extension") -> str:
        with self.conn:
            loan = self.conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
            if not loan or loan["loan_state"] not in ("GRACE_REPAYMENT", "ACTIVE_REPAYMENT"):
                raise ValueError("Loan ineligible for hardship modification")
            new_grace = loan["grace_days"] + extra_grace_days
            self.conn.execute("UPDATE loans SET loan_state = 'HARDSHIP_MODIFIED', grace_days = ?, updated_at = ? WHERE loan_id = ?", (new_grace, utc_now_iso(), loan_id))
            self._append_event("LOAN_05_HARDSHIP_MODIFICATION", "LOAN", str(loan_id), loan["loan_state"], "HARDSHIP_MODIFIED", None, 0, {"extra_grace_days": extra_grace_days, "new_grace_days": new_grace, "late_fee_cents": 0, "note": note})
            return "HARDSHIP_MODIFIED"

    def resume_repayment_on_new_job(self: CoopBankLedger, member_token: str, case_token: str, loan_id: int, job_note: str = "New job secured") -> str:
        with self.conn:
            m = self.conn.execute("SELECT * FROM members WHERE member_token = ?", (member_token,)).fetchone()
            loan = self.conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
            if not m or not loan or loan["loan_state"] not in ("GRACE_REPAYMENT", "HARDSHIP_MODIFIED"):
                raise ValueError("Cannot resume repayment")
            now = utc_now_iso()
            self.conn.execute("UPDATE members SET standing_state = 'ACTIVE_STANDING', grace_days_remaining = 0, updated_at = ? WHERE member_token = ?", (now, member_token))
            self._append_event("LOAN_06_REEMPLOYMENT_RESUMPTION", "MEMBER", member_token, m["standing_state"], "ACTIVE_STANDING", None, 0, {"case_token": case_token, "loan_id": loan_id})
            self.conn.execute("INSERT INTO case_milestones (case_token, member_token, milestone_stage, outcome_code, rule_codes_json, lesson_note, recorded_at) VALUES (?, ?, 'JOB_SEARCH_TRACK_ACTIVE', 'NEW_JOB_SECURED', ?, ?, ?)", (case_token, member_token, json.dumps(["LOAN_06_REEMPLOYMENT_RESUMPTION"]), job_note, now))
            self.conn.execute("UPDATE loans SET loan_state = 'ACTIVE_REPAYMENT', updated_at = ? WHERE loan_id = ?", (now, loan_id))
            self._append_event("LOAN_06_REEMPLOYMENT_RESUMPTION", "LOAN", str(loan_id), loan["loan_state"], "ACTIVE_REPAYMENT", None, 0, {"remaining_cents": loan["remaining_cents"], "note": job_note})
            return "ACTIVE_REPAYMENT"

    def write_off_loan_to_reserve(self: CoopBankLedger, loan_id: int, reason: str = "Hardship write-off", mode: str = "reject") -> int:
        assert mode in ("reject", "cap")
        with self.conn:
            loan = self.conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
            res = self.conn.execute("SELECT balance_cents FROM buckets WHERE bucket_code = 'BUCKET_HARDSHIP_RESERVE'").fetchone()
            unpaid, avail = int(loan["remaining_cents"]), int(res["balance_cents"])
            if unpaid > avail and mode == "reject":
                raise ValueError(f"Reserve shortfall: unpaid={unpaid}c > reserve={avail}c")
            absorbed = min(unpaid, avail) if mode == "cap" else unpaid
            rem = unpaid - absorbed
            next_s = "WRITTEN_OFF_RESERVE" if rem == 0 else "HARDSHIP_MODIFIED"
            if absorbed > 0:
                self._adjust_bucket("BUCKET_HARDSHIP_RESERVE", -absorbed, "LOAN_07_RESERVE_WRITE_OFF", f"LOAN_{loan_id}", reason)
            self.conn.execute("UPDATE loans SET remaining_cents = ?, loan_state = ?, updated_at = ? WHERE loan_id = ?", (rem, next_s, utc_now_iso(), loan_id))
            self._append_event("LOAN_07_RESERVE_WRITE_OFF", "LOAN", str(loan_id), loan["loan_state"], next_s, "BUCKET_HARDSHIP_RESERVE", -absorbed, {"mode": mode, "absorbed_cents": absorbed, "remaining_cents": rem, "reason": reason})
            return absorbed

    def record_loan_repayment(self: CoopBankLedger, loan_id: int, payment_cents: int, note: str = "Repayment") -> str:
        with self.conn:
            loan = self.conn.execute("SELECT * FROM loans WHERE loan_id = ?", (loan_id,)).fetchone()
            applied = min(payment_cents, loan["remaining_cents"])
            rem = loan["remaining_cents"] - applied
            next_s = "CLOSED_PAID" if rem == 0 else "ACTIVE_REPAYMENT"
            self._adjust_bucket("BUCKET_LENDING_POOL", applied, "LOAN_04_POOL_REPLENISHMENT", f"LOAN_{loan_id}", note)
            self.conn.execute("UPDATE loans SET remaining_cents = ?, loan_state = ?, updated_at = ? WHERE loan_id = ?", (rem, next_s, utc_now_iso(), loan_id))
            self._append_event("LOAN_04_POOL_REPLENISHMENT", "LOAN", str(loan_id), loan["loan_state"], next_s, "BUCKET_LENDING_POOL", applied, {"applied_cents": applied, "remaining_cents": rem})
            return next_s

    def verify_chain(self: CoopBankLedger) -> Tuple[bool, int]:
        rows = self.conn.execute("SELECT * FROM ledger_events ORDER BY event_id ASC").fetchall()
        exp = "GENESIS_" + "0" * 56
        for r in rows:
            if r["prev_hash"] != exp or compute_event_hash(r["prev_hash"], r["occurred_at"], r["rule_code"], r["entity_type"], r["entity_id"], r["from_state"], r["to_state"], r["bucket_code"], r["delta_cents"], r["payload_json"]) != r["event_hash"]:
                return False, r["event_id"]
            exp = r["event_hash"]
        return True, len(rows)

    def summary(self: CoopBankLedger) -> Dict[str, Any]:
        ok, cnt = self.verify_chain()
        return {
            "chain_valid": ok,
            "total_ledger_events": cnt,
            "latest_hash": self._latest_hash(),
            "buckets": [dict(r) for r in self.conn.execute("SELECT * FROM buckets ORDER BY bucket_code").fetchall()],
            "loans": [dict(r) for r in self.conn.execute("SELECT * FROM loans ORDER BY loan_id").fetchall()],
        }

    cls.create_and_process_loan = create_and_process_loan
    cls.modify_loan_hardship = modify_loan_hardship
    cls.resume_repayment_on_new_job = resume_repayment_on_new_job
    cls.write_off_loan_to_reserve = write_off_loan_to_reserve
    cls.record_loan_repayment = record_loan_repayment
    cls.verify_chain = verify_chain
    cls.summary = summary
    return cls

_loan_methods(CoopBankLedger)


def run_tamper_test() -> dict:
    db = CoopBankLedger(":memory:")
    db.enroll_member("[Member_002]", 2)
    db.post_dues("[Member_002]", 20000, 1)
    lid = db.create_and_process_loan("[Member_002]", "JOB_SEARCH_LOAN", 8000, 30)
    db.record_loan_repayment(lid, 2000, "Installment")
    ok_before, cnt = db.verify_chain()
    assert ok_before and cnt >= 3
    orig = db.conn.execute("SELECT delta_cents FROM ledger_events WHERE event_id = 3").fetchone()["delta_cents"]
    with db.conn:
        db.conn.execute("UPDATE ledger_events SET delta_cents = ? WHERE event_id = 3", (orig + 1,))
    ok_tamper, bad_id = db.verify_chain()
    assert not ok_tamper and bad_id == 3
    with db.conn:
        db.conn.execute("UPDATE ledger_events SET delta_cents = ? WHERE event_id = 3", (orig,))
    ok_after, _ = db.verify_chain()
    assert ok_after
    return {"passed": True, "events": cnt, "detected_event_id": bad_id}


def run_stress_test(mode: str = "cap") -> dict:
    db = CoopBankLedger(":memory:")
    for t in ("[Member_001]", "[Member_002]", "[Member_003]"):
        db.enroll_member(t, 1)
        for _ in range(3):
            db.post_dues(t, 7500, 1)
    db.record_case_milestone("[Case_002]", "[Member_002]", "RESOLUTION_OR_APPEAL_OUTCOME", "DISMISSED_APPEAL", ["RULE_01", "DFR_01"], "Appeal dismissed; job search active.")
    db.transition_standing("[Member_002]", "GRACE_TRANSITION", "Post-appeal support", 60, True)
    l1 = db.create_and_process_loan("[Member_001]", "BRIDGE_LOAN", 10000, 30)
    l2 = db.create_and_process_loan("[Member_002]", "JOB_SEARCH_LOAN", 12000, 45)
    l3 = db.create_and_process_loan("[Member_003]", "BRIDGE_LOAN", 5000, 30)
    db.resume_repayment_on_new_job("[Member_002]", "[Case_002]", l2, "New role started")
    db.record_loan_repayment(l2, 4000, "First repayment")
    rejected, off = False, 0
    try:
        off = db.write_off_loan_to_reserve(l3, "Hardship write-off", mode=mode)
    except ValueError:
        if mode == "reject":
            rejected = True
        else:
            raise
    return {"mode": mode, "shortfall_rejected": rejected, "written_off_cents": off, "loans": {"l1": l1, "l2": l2, "l3": l3}, "summary": db.summary()}



def _attach_export_bundle() -> None:
    def export_audit_bundle(self: CoopBankLedger, out_path: str) -> Dict[str, Any]:
        chain_ok, event_count = self.verify_chain()
        if not chain_ok:
            raise ValueError(f"Cannot export audit bundle: chain invalid at event_id={event_count}")

        events = [
            dict(r) for r in self.conn.execute("SELECT * FROM ledger_events ORDER BY event_id ASC").fetchall()
        ]
        body = {
            "exported_at": utc_now_iso(),
            "chain_verified": True,
            "event_count": event_count,
            "latest_hash": self._latest_hash(),
            "summary": self.summary(),
            "ledger_events": events,
        }
        canonical_body = json.dumps(body, sort_keys=True, ensure_ascii=False)
        bundle_sha256 = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()

        bundle = {
            "bundle_sha256": bundle_sha256,
            **body,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=2, ensure_ascii=False)

        return {
            "out_path": out_path,
            "bundle_sha256": bundle_sha256,
            "event_count": event_count,
            "chain_verified": True,
        }

    CoopBankLedger.export_audit_bundle = export_audit_bundle


_attach_export_bundle()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["reject", "cap"], default="cap")
    p.add_argument("--test", choices=["all", "stress", "tamper"], default="all")
    p.add_argument("--export-bundle", default=None, help="Write SHA-256 verified JSON audit bundle to path")
    args = p.parse_args()
    out: Dict[str, Any] = {"mode": args.mode, "test": args.test}
    if args.test in ("all", "tamper"):
        out["tamper_test"] = run_tamper_test()
    if args.test in ("all", "stress"):
        db = CoopBankLedger(":memory:")
        for t in ("[Member_001]", "[Member_002]", "[Member_003]"):
            db.enroll_member(t, 1)
            for _ in range(3):
                db.post_dues(t, 7500, 1)
        db.record_case_milestone("[Case_002]", "[Member_002]", "RESOLUTION_OR_APPEAL_OUTCOME", "DISMISSED_APPEAL", ["RULE_01", "DFR_01"], "Appeal dismissed; job search active.")
        db.transition_standing("[Member_002]", "GRACE_TRANSITION", "Post-appeal support", 60, True)
        l1 = db.create_and_process_loan("[Member_001]", "BRIDGE_LOAN", 10000, 30)
        l2 = db.create_and_process_loan("[Member_002]", "JOB_SEARCH_LOAN", 12000, 45)
        l3 = db.create_and_process_loan("[Member_003]", "BRIDGE_LOAN", 5000, 30)
        db.resume_repayment_on_new_job("[Member_002]", "[Case_002]", l2, "New role started")
        db.record_loan_repayment(l2, 4000, "First repayment")
        rejected, off = False, 0
        try:
            off = db.write_off_loan_to_reserve(l3, "Hardship write-off", mode=args.mode)
        except ValueError:
            if args.mode == "reject":
                rejected = True
            else:
                raise
        out["stress_test"] = {
            "mode": args.mode,
            "shortfall_rejected": rejected,
            "written_off_cents": off,
            "loans": {"l1": l1, "l2": l2, "l3": l3},
            "summary": db.summary(),
        }
        if args.export_bundle:
            out["audit_bundle"] = db.export_audit_bundle(args.export_bundle)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

def _bundle_methods(cls: Any) -> Any:
    def export_audit_bundle(self: CoopBankLedger, out_path: str) -> Dict[str, Any]:
        chain_ok, event_count = self.verify_chain()
        if not chain_ok:
            raise ValueError(f"Cannot export audit bundle: chain invalid at event_id={event_count}")

        events = [
            dict(r) for r in self.conn.execute("SELECT * FROM ledger_events ORDER BY event_id ASC").fetchall()
        ]
        body = {
            "exported_at": utc_now_iso(),
            "chain_verified": True,
            "event_count": event_count,
            "latest_hash": self._latest_hash(),
            "summary": self.summary(),
            "ledger_events": events,
        }
        canonical_body = json.dumps(body, sort_keys=True, ensure_ascii=False)
        bundle_sha256 = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()

        bundle = {
            "bundle_sha256": bundle_sha256,
            **body,
        }
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh, indent=2, ensure_ascii=False)

        return {
            "out_path": out_path,
            "bundle_sha256": bundle_sha256,
            "event_count": event_count,
            "chain_verified": True,
        }

    cls.export_audit_bundle = export_audit_bundle
    return cls

_bundle_methods(CoopBankLedger)
