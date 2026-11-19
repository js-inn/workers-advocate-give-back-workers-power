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



def _attach_iso12207_check() -> None:
    def _reconcile_bucket_deltas(events: List[Dict[str, Any]]) -> Dict[str, int]:
        sums: Dict[str, int] = {}
        seen_keys = set()
        for ev in events:
            bcode = ev.get("bucket_code")
            if not bcode:
                continue
            rule = ev.get("rule_code") or ""
            delta = int(ev.get("delta_cents") or 0)
            try:
                payload = json.loads(ev.get("payload_json") or "{}")
            except Exception:
                payload = {}

            if rule == "DUES_01_ATOMIC_SPLIT":
                key = ("dues", int(ev["event_id"]), bcode, delta)
                if key not in seen_keys:
                    seen_keys.add(key)
                    sums[bcode] = sums.get(bcode, 0) + delta
            elif rule == "LOAN_02_LIQUIDITY_AND_CAP" and payload.get("reason") == "Disbursement":
                key = ("disburse", payload.get("ref_id"), bcode, delta)
                if key not in seen_keys:
                    seen_keys.add(key)
                    sums[bcode] = sums.get(bcode, 0) + delta
            elif rule == "LOAN_04_POOL_REPLENISHMENT" and ("applied_cents" in payload or payload.get("reason") == "Repay"):
                amt = int(payload["applied_cents"]) if "applied_cents" in payload else abs(delta)
                ref = payload.get("ref_id") or ev.get("entity_id")
                key = ("repay", ref, bcode, amt, int(ev["event_id"]) // 2)
                if key not in seen_keys:
                    seen_keys.add(key)
                    sums[bcode] = sums.get(bcode, 0) + amt
            elif rule == "LOAN_07_RESERVE_WRITE_OFF" and "absorbed_cents" in payload:
                amt = abs(int(payload["absorbed_cents"]))
                key = ("writeoff", ev.get("entity_id"), bcode, amt)
                if key not in seen_keys:
                    seen_keys.add(key)
                    sums[bcode] = sums.get(bcode, 0) + (-amt)
        return sums

    def verify_iso12207_compliance(self: CoopBankLedger, bundle_path: Optional[str] = None) -> Dict[str, Any]:
        chain_ok, _ = self.verify_chain()
        buckets = [dict(r) for r in self.conn.execute("SELECT * FROM buckets ORDER BY bucket_code").fetchall()]
        events = [dict(r) for r in self.conn.execute("SELECT * FROM ledger_events ORDER BY event_id ASC").fetchall()]

        event_sums = _reconcile_bucket_deltas(events)
        missing_trace_events: List[int] = [
            int(ev["event_id"])
            for ev in events
            if not ev.get("rule_code") or not ev.get("entity_type") or not ev.get("entity_id") or not ev.get("occurred_at")
        ]
        bucket_mismatches = [
            bk["bucket_code"]
            for bk in buckets
            if int(bk["balance_cents"]) != event_sums.get(bk["bucket_code"], 0)
        ]

        expected_shares = {
            "BUCKET_LENDING_POOL": 0.55,
            "BUCKET_LEGAL_DEFENSE": 0.25,
            "BUCKET_HARDSHIP_RESERVE": 0.20,
        }
        share_mismatches = [
            bk["bucket_code"]
            for bk in buckets
            if abs(float(bk["target_share"]) - expected_shares.get(bk["bucket_code"], -1.0)) > 1e-9
        ]

        bundle_ok = None
        if bundle_path:
            with open(bundle_path, encoding="utf-8") as fh:
                b = json.load(fh)
            stored = b.get("bundle_sha256")
            body = {k: v for k, v in b.items() if k != "bundle_sha256"}
            recomputed = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            bundle_ok = (stored == recomputed)

        checks = {
            "iso12207_configuration_chain_integrity": bool(chain_ok),
            "iso12207_ledger_bucket_reconciliation": len(bucket_mismatches) == 0,
            "iso12207_governance_bucket_shares": len(share_mismatches) == 0,
            "iso12207_audit_event_traceability": len(missing_trace_events) == 0,
        }
        if bundle_ok is not None:
            checks["iso12207_export_bundle_digest"] = bool(bundle_ok)

        return {
            "standard": "ISO/IEC 12207",
            "compliant": all(checks.values()),
            "checks": checks,
            "event_count": len(events),
            "reconciled_bucket_sums": event_sums,
            "bucket_mismatches": bucket_mismatches,
            "share_mismatches": share_mismatches,
            "missing_trace_events": missing_trace_events,
            "bundle_path": bundle_path,
        }

    CoopBankLedger.verify_iso12207_compliance = verify_iso12207_compliance


_attach_iso12207_check()


def _attach_strike_necessity_gate() -> None:
    def evaluate_strike_necessity(
        self: CoopBankLedger,
        unit_id: str,
        total_disciplinary_signings: int,
        invalid_disciplinary_signings: int,
        total_grievances: int,
        unresolved_grievances: int,
        contract_expired: bool,
        mediation_exhausted: bool,
        real_wage_gap_pct: float,
        min_strike_runway_cents: int,
        employer_operating_margin_pct: float = 0.08,
        total_safety_checks: int = 100,
        unresolved_safety_hazards: int = 0,
        breach_threshold: float = 0.15,
        grievance_threshold: float = 0.10,
        wage_gap_threshold: float = 0.03,
        healthy_margin_threshold: float = 0.10,
        strained_margin_threshold: float = 0.03,
        safety_deficit_threshold: float = 0.10,
        max_strained_wage_demand_pct: float = 0.04,
    ) -> Dict[str, Any]:
        if total_disciplinary_signings < 0 or invalid_disciplinary_signings < 0:
            raise ValueError("Disciplinary signing counts must be non-negative")
        if invalid_disciplinary_signings > total_disciplinary_signings:
            raise ValueError("Invalid signings cannot exceed total signings")
        if total_grievances < 0 or unresolved_grievances < 0 or unresolved_grievances > total_grievances:
            raise ValueError("Invalid grievance counts")
        if total_safety_checks < 0 or unresolved_safety_hazards < 0 or unresolved_safety_hazards > total_safety_checks:
            raise ValueError("Invalid safety check/hazard counts")

        proc_breach_rate = (
            round(invalid_disciplinary_signings / total_disciplinary_signings, 6)
            if total_disciplinary_signings > 0
            else 0.0
        )
        unresolved_rate = (
            round(unresolved_grievances / total_grievances, 6)
            if total_grievances > 0
            else 0.0
        )
        safety_deficit_rate = (
            round(unresolved_safety_hazards / total_safety_checks, 6)
            if total_safety_checks > 0
            else 0.0
        )

        margin_pct = round(float(employer_operating_margin_pct), 6)
        wage_gap = round(float(real_wage_gap_pct), 6)

        high_margin = margin_pct >= healthy_margin_threshold
        low_margin = margin_pct < strained_margin_threshold
        safety_or_fairness_deficit = (safety_deficit_rate > safety_deficit_threshold) or (
            proc_breach_rate > breach_threshold
        )

        profit_over_safety = bool(high_margin and safety_or_fairness_deficit)
        low_margin_over_demand = bool(low_margin and (wage_gap > max_strained_wage_demand_pct))

        if profit_over_safety and low_margin_over_demand:
            balance_flag = "CONFLICTED_EXTREME"
        elif profit_over_safety:
            balance_flag = "PROFIT_OVER_SAFETY"
        elif low_margin_over_demand:
            balance_flag = "LOW_MARGIN_OVER_DEMAND"
        else:
            balance_flag = "BALANCED_EQUILIBRIUM"

        buckets = {
            r["bucket_code"]: int(r["balance_cents"])
            for r in self.conn.execute("SELECT bucket_code, balance_cents FROM buckets").fetchall()
        }
        hardship_cents = buckets.get("BUCKET_HARDSHIP_RESERVE", 0)
        lending_cents = buckets.get("BUCKET_LENDING_POOL", 0)
        available_runway_cents = hardship_cents + lending_cents

        proc_breach_triggered = proc_breach_rate > breach_threshold
        grievance_backlog_triggered = unresolved_rate > grievance_threshold
        systemic_breach = proc_breach_triggered or grievance_backlog_triggered or (
            safety_deficit_rate > safety_deficit_threshold
        )
        bargaining_impasse = bool(
            contract_expired and mediation_exhausted and (wage_gap > wage_gap_threshold)
        )
        liquidity_ready = available_runway_cents >= int(min_strike_runway_cents)

        if low_margin_over_demand and not profit_over_safety:
            verdict = "MEDIATION_OVER_DEMAND_CHECK"
        elif systemic_breach and bargaining_impasse and liquidity_ready:
            verdict = "STRIKE_NECESSARY"
        elif systemic_breach and bargaining_impasse and not liquidity_ready:
            verdict = "STRIKE_DEFERRED_INSUFFICIENT_RESERVE"
        elif systemic_breach or bargaining_impasse:
            verdict = "MEDIATION_ALERT"
        else:
            verdict = "STRIKE_NOT_NECESSARY"

        payload = {
            "unit_id": unit_id,
            "verdict": verdict,
            "balance_flag": balance_flag,
            "profit_over_safety": profit_over_safety,
            "low_margin_over_demand": low_margin_over_demand,
            "employer_operating_margin_pct": margin_pct,
            "safety_deficit_rate": safety_deficit_rate,
            "procedural_breach_rate": proc_breach_rate,
            "unresolved_grievance_rate": unresolved_rate,
            "bargaining_impasse": bargaining_impasse,
            "real_wage_gap_pct": wage_gap,
            "liquidity_ready": liquidity_ready,
            "available_runway_cents": available_runway_cents,
            "min_strike_runway_cents": int(min_strike_runway_cents),
        }

        if hasattr(self, "_append_event"):
            prev_row = self.conn.execute(
                """
                SELECT to_state FROM ledger_events
                WHERE rule_code = ? AND entity_id = ?
                ORDER BY event_id DESC LIMIT 1
                """,
                ("GOVERNANCE_01_STRIKE_NECESSITY", unit_id),
            ).fetchone()
            from_state = prev_row["to_state"] if prev_row and prev_row["to_state"] else "UNASSESSED"
            self._append_event(
                rule_code="GOVERNANCE_01_STRIKE_NECESSITY",
                entity_type="BARGAINING_UNIT",
                entity_id=unit_id,
                from_state=from_state,
                to_state=verdict,
                bucket_code=None,
                delta_cents=0,
                payload=payload,
            )
        else:
            last = self.conn.execute(
                "SELECT event_id, event_hash FROM ledger_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
            prev_hash = last["event_hash"] if last else "0" * 64
            occurred_at = datetime.now(timezone.utc).isoformat()
            payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            raw = f"{prev_hash}|GOVERNANCE_01_STRIKE_NECESSITY|BARGAINING_UNIT|{unit_id}||0|{payload_json}|{occurred_at}"
            event_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            self.conn.execute(
                """
                INSERT INTO ledger_events (
                    rule_code, entity_type, entity_id, bucket_code, delta_cents,
                    payload_json, occurred_at, prev_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "GOVERNANCE_01_STRIKE_NECESSITY",
                    "BARGAINING_UNIT",
                    unit_id,
                    None,
                    0,
                    payload_json,
                    occurred_at,
                    prev_hash,
                    event_hash,
                ),
            )
            self.conn.commit()

        return payload

    CoopBankLedger.evaluate_strike_necessity = evaluate_strike_necessity


_attach_strike_necessity_gate()

def _attach_mortgage_strike_deferral_rule() -> None:
    def _ensure_mortgages_table(self: CoopBankLedger) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mortgages (
                mortgage_id TEXT PRIMARY KEY,
                member_token TEXT NOT NULL,
                unit_id TEXT NOT NULL,
                principal_cents INTEGER NOT NULL,
                remaining_principal_cents INTEGER NOT NULL,
                monthly_payment_cents INTEGER NOT NULL,
                deferred_balance_cents INTEGER NOT NULL DEFAULT 0,
                deferred_months_used INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'MORTGAGE_ACTIVE',
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def create_mortgage(
        self: CoopBankLedger,
        mortgage_id: str,
        member_token: str,
        unit_id: str,
        principal_cents: int,
        monthly_payment_cents: int,
    ) -> Dict[str, Any]:
        assert member_token.startswith("[Member_"), f"Invalid member token: {member_token}"
        if principal_cents <= 0 or monthly_payment_cents <= 0:
            raise ValueError("Principal and monthly payment must be positive cents")
        self._ensure_mortgages_table()
        occurred_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO mortgages (
                mortgage_id, member_token, unit_id, principal_cents,
                remaining_principal_cents, monthly_payment_cents,
                deferred_balance_cents, deferred_months_used, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, 'MORTGAGE_ACTIVE', ?)
            """,
            (
                mortgage_id,
                member_token,
                unit_id,
                int(principal_cents),
                int(principal_cents),
                int(monthly_payment_cents),
                occurred_at,
            ),
        )
        payload = {
            "mortgage_id": mortgage_id,
            "member_token": member_token,
            "unit_id": unit_id,
            "principal_cents": int(principal_cents),
            "monthly_payment_cents": int(monthly_payment_cents),
            "status": "MORTGAGE_ACTIVE",
        }
        if hasattr(self, "_append_event"):
            self._append_event(
                rule_code="MORTGAGE_00_ORIGINATED",
                entity_type="MORTGAGE",
                entity_id=mortgage_id,
                from_state="NONE",
                to_state="MORTGAGE_ACTIVE",
                bucket_code=None,
                delta_cents=0,
                payload=payload,
            )
        else:
            self.conn.commit()
        return payload

    def apply_strike_mortgage_deferral(
        self: CoopBankLedger,
        mortgage_id: str,
        months_requested: int = 1,
        max_deferral_months: int = 3,
        min_reserve_floor_cents: int = 0,
        mode: str = "cap",
    ) -> Dict[str, Any]:
        if months_requested <= 0:
            raise ValueError("months_requested must be positive")
        if mode not in ("reject", "cap"):
            raise ValueError("mode must be 'reject' or 'cap'")
        self._ensure_mortgages_table()

        row = self.conn.execute(
            "SELECT * FROM mortgages WHERE mortgage_id = ?",
            (mortgage_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown mortgage_id: {mortgage_id}")

        unit_id = row["unit_id"]
        verdict_row = self.conn.execute(
            """
            SELECT to_state FROM ledger_events
            WHERE rule_code = 'GOVERNANCE_01_STRIKE_NECESSITY' AND entity_id = ?
            ORDER BY event_id DESC LIMIT 1
            """,
            (unit_id,),
        ).fetchone()
        latest_verdict = verdict_row["to_state"] if verdict_row else "UNASSESSED"
        if latest_verdict != "STRIKE_NECESSARY":
            raise ValueError(
                f"Mortgage strike deferral requires STRIKE_NECESSARY for {unit_id}, got {latest_verdict}"
            )

        used_months = int(row["deferred_months_used"])
        allowed_months = max(0, int(max_deferral_months) - used_months)
        effective_months = min(int(months_requested), allowed_months)
        if effective_months <= 0:
            raise ValueError(
                f"Deferral cap reached for {mortgage_id}: used={used_months}, max={max_deferral_months}"
            )

        requested_cents = effective_months * int(row["monthly_payment_cents"])
        bucket_row = self.conn.execute(
            "SELECT balance_cents FROM buckets WHERE bucket_code = 'BUCKET_HARDSHIP_RESERVE'"
        ).fetchone()
        reserve_cents = int(bucket_row["balance_cents"]) if bucket_row else 0
        spendable_reserve = max(0, reserve_cents - int(min_reserve_floor_cents))

        if mode == "reject" and requested_cents > spendable_reserve:
            raise ValueError(
                f"Mortgage deferral reserve shortfall: requested={requested_cents}c > available={spendable_reserve}c"
            )

        deferred_cents = requested_cents if mode == "reject" else min(requested_cents, spendable_reserve)
        uncovered_cents = max(0, requested_cents - deferred_cents)
        months_granted = deferred_cents // int(row["monthly_payment_cents"])
        next_status = "STRIKE_DEFERRED" if uncovered_cents == 0 else "STRIKE_DEFERRED_PARTIAL"

        new_deferred_balance = int(row["deferred_balance_cents"]) + deferred_cents
        new_months_used = used_months + months_granted

        self.conn.execute(
            """
            UPDATE mortgages
            SET deferred_balance_cents = ?, deferred_months_used = ?, status = ?
            WHERE mortgage_id = ?
            """,
            (new_deferred_balance, new_months_used, next_status, mortgage_id),
        )

        payload = {
            "mortgage_id": mortgage_id,
            "member_token": row["member_token"],
            "unit_id": unit_id,
            "strike_verdict": latest_verdict,
            "mode": mode,
            "requested_cents": requested_cents,
            "deferred_cents": deferred_cents,
            "uncovered_cents": uncovered_cents,
            "months_granted": months_granted,
            "deferred_months_used": new_months_used,
            "deferred_balance_cents": new_deferred_balance,
            "min_reserve_floor_cents": int(min_reserve_floor_cents),
            "status": next_status,
        }

        from_state = row["status"]
        if hasattr(self, "_append_event"):
            self._append_event(
                rule_code="MORTGAGE_01_STRIKE_DEFERRAL",
                entity_type="MORTGAGE",
                entity_id=mortgage_id,
                from_state=from_state,
                to_state=next_status,
                bucket_code="BUCKET_HARDSHIP_RESERVE",
                delta_cents=0,
                payload=payload,
            )
        else:
            self.conn.commit()

        return payload

    CoopBankLedger._ensure_mortgages_table = _ensure_mortgages_table
    CoopBankLedger.create_mortgage = create_mortgage
    CoopBankLedger.apply_strike_mortgage_deferral = apply_strike_mortgage_deferral

    def resume_mortgage_after_strike(
        self: CoopBankLedger,
        mortgage_id: str,
        repayment_mode: str = "append_term",
        repayment_months: int = 12,
        require_strike_resolved: bool = False,
    ) -> Dict[str, Any]:
        if repayment_mode not in ("append_term", "recalculate_installment"):
            raise ValueError("repayment_mode must be 'append_term' or 'recalculate_installment'")
        if repayment_months <= 0:
            raise ValueError("repayment_months must be positive")

        self._ensure_mortgages_table()
        row = self.conn.execute(
            "SELECT * FROM mortgages WHERE mortgage_id = ?",
            (mortgage_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"Unknown mortgage_id: {mortgage_id}")

        from_state = row["status"]
        if from_state not in ("STRIKE_DEFERRED", "STRIKE_DEFERRED_PARTIAL"):
            raise ValueError(
                f"Cannot resume mortgage {mortgage_id} from state {from_state}; expected STRIKE_DEFERRED or STRIKE_DEFERRED_PARTIAL"
            )

        unit_id = row["unit_id"]
        verdict_row = self.conn.execute(
            """
            SELECT to_state FROM ledger_events
            WHERE rule_code = 'GOVERNANCE_01_STRIKE_NECESSITY' AND entity_id = ?
            ORDER BY event_id DESC LIMIT 1
            """,
            (unit_id,),
        ).fetchone()
        latest_verdict = verdict_row["to_state"] if verdict_row else "UNASSESSED"
        if require_strike_resolved and latest_verdict == "STRIKE_NECESSARY":
            raise ValueError(
                f"Strike still active (STRIKE_NECESSARY) for {unit_id}; cannot resume with require_strike_resolved=True"
            )

        deferred_cents = int(row["deferred_balance_cents"])
        monthly_cents = int(row["monthly_payment_cents"])
        estimated_added_months = math.ceil(deferred_cents / monthly_cents) if deferred_cents > 0 else 0
        additional_monthly_cents = (
            math.ceil(deferred_cents / repayment_months)
            if (repayment_mode == "recalculate_installment" and deferred_cents > 0)
            else 0
        )

        next_status = "MORTGAGE_ACTIVE"
        self.conn.execute(
            """
            UPDATE mortgages
            SET status = ?
            WHERE mortgage_id = ?
            """,
            (next_status, mortgage_id),
        )

        payload = {
            "mortgage_id": mortgage_id,
            "member_token": row["member_token"],
            "unit_id": unit_id,
            "strike_verdict_at_resume": latest_verdict,
            "repayment_mode": repayment_mode,
            "deferred_balance_cents": deferred_cents,
            "deferred_months_used": int(row["deferred_months_used"]),
            "estimated_added_months": estimated_added_months,
            "additional_monthly_cents": additional_monthly_cents,
            "repayment_months": int(repayment_months),
            "status": next_status,
        }

        if hasattr(self, "_append_event"):
            self._append_event(
                rule_code="MORTGAGE_02_POST_STRIKE_RESUME",
                entity_type="MORTGAGE",
                entity_id=mortgage_id,
                from_state=from_state,
                to_state=next_status,
                bucket_code=None,
                delta_cents=0,
                payload=payload,
            )
        else:
            self.conn.commit()

        return payload

    CoopBankLedger.resume_mortgage_after_strike = resume_mortgage_after_strike

    def _ensure_retiree_table(self: CoopBankLedger) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retiree_members (
                member_token TEXT PRIMARY KEY,
                tenure_months INTEGER NOT NULL,
                retiree_hardship_cap_cents INTEGER NOT NULL,
                retiree_hardship_used_cents INTEGER NOT NULL DEFAULT 0,
                mortgage_shield_active INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'MEMBER_RETIRED',
                retired_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def retire_member(
        self: CoopBankLedger,
        member_token: str,
        tenure_months: int,
        min_retirement_tenure_months: int = 60,
        retiree_hardship_cap_cents: int = 500_000,
    ) -> Dict[str, Any]:
        assert member_token.startswith("[Member_"), f"Invalid member token: {member_token}"
        if tenure_months < int(min_retirement_tenure_months):
            raise ValueError(
                f"Member {member_token} tenure_months={tenure_months} below minimum {min_retirement_tenure_months}"
            )
        if retiree_hardship_cap_cents < 0:
            raise ValueError("retiree_hardship_cap_cents must be non-negative")

        self._ensure_retiree_table()
        existing = self.conn.execute(
            "SELECT * FROM retiree_members WHERE member_token = ?",
            (member_token,),
        ).fetchone()
        if existing:
            raise ValueError(f"Member {member_token} is already in {existing['status']}")

        # Check if member holds any existing mortgage and protect it from retirement recall/acceleration
        mortgages_table = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mortgages'"
        ).fetchone()
        protected_mortgages: list[str] = []
        if mortgages_table:
            rows = self.conn.execute(
                "SELECT mortgage_id, status FROM mortgages WHERE member_token = ?",
                (member_token,),
            ).fetchall()
            for m in rows:
                protected_mortgages.append(m["mortgage_id"])
                if m["status"] == "MORTGAGE_RECALLED":
                    self.conn.execute(
                        "UPDATE mortgages SET status = 'MORTGAGE_ACTIVE' WHERE mortgage_id = ?",
                        (m["mortgage_id"],),
                    )

        retired_at = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO retiree_members (
                member_token, tenure_months, retiree_hardship_cap_cents,
                retiree_hardship_used_cents, mortgage_shield_active, status, retired_at
            ) VALUES (?, ?, ?, 0, 1, 'MEMBER_RETIRED', ?)
            """,
            (
                member_token,
                int(tenure_months),
                int(retiree_hardship_cap_cents),
                retired_at,
            ),
        )

        payload = {
            "member_token": member_token,
            "status": "MEMBER_RETIRED",
            "tenure_months": int(tenure_months),
            "min_retirement_tenure_months": int(min_retirement_tenure_months),
            "retiree_hardship_cap_cents": int(retiree_hardship_cap_cents),
            "retiree_hardship_used_cents": 0,
            "mortgage_shield_active": True,
            "protected_mortgage_ids": protected_mortgages,
        }

        if hasattr(self, "_append_event"):
            self._append_event(
                rule_code="MEMBER_01_RETIREMENT_TRANSITION",
                entity_type="MEMBER",
                entity_id=member_token,
                from_state="ACTIVE",
                to_state="MEMBER_RETIRED",
                bucket_code=None,
                delta_cents=0,
                payload=payload,
            )
        else:
            self.conn.commit()

        return payload

    def claim_retiree_hardship(
        self: CoopBankLedger,
        member_token: str,
        requested_cents: int,
        min_reserve_floor_cents: int = 0,
        mode: str = "cap",
    ) -> Dict[str, Any]:
        if requested_cents <= 0:
            raise ValueError("requested_cents must be positive")
        if min_reserve_floor_cents < 0:
            raise ValueError("min_reserve_floor_cents must be non-negative")
        if mode not in ("reject", "cap"):
            raise ValueError("mode must be 'reject' or 'cap'")

        self._ensure_retiree_table()
        row = self.conn.execute(
            "SELECT * FROM retiree_members WHERE member_token = ?",
            (member_token,),
        ).fetchone()
        if not row or row["status"] != "MEMBER_RETIRED":
            raise ValueError(f"Member {member_token} is not in MEMBER_RETIRED status")

        used_cents = int(row["retiree_hardship_used_cents"])
        cap_cents = int(row["retiree_hardship_cap_cents"])
        remaining_member_cap = max(0, cap_cents - used_cents)

        bucket_row = self.conn.execute(
            "SELECT balance_cents FROM buckets WHERE bucket_code = 'BUCKET_HARDSHIP_RESERVE'"
        ).fetchone()
        reserve_cents = int(bucket_row["balance_cents"]) if bucket_row else 0
        spendable_reserve = max(0, reserve_cents - int(min_reserve_floor_cents))

        max_allowable = min(remaining_member_cap, spendable_reserve)
        if mode == "reject" and requested_cents > max_allowable:
            raise ValueError(
                f"Retiree hardship claim exceeds cap/reserve: requested={requested_cents}c, allowable={max_allowable}c"
            )

        granted_cents = requested_cents if mode == "reject" else min(int(requested_cents), max_allowable)
        uncovered_cents = max(0, int(requested_cents) - granted_cents)
        new_used_cents = used_cents + granted_cents
        new_reserve_cents = reserve_cents - granted_cents

        self.conn.execute(
            """
            UPDATE retiree_members
            SET retiree_hardship_used_cents = ?
            WHERE member_token = ?
            """,
            (new_used_cents, member_token),
        )
        if bucket_row and granted_cents > 0:
            self.conn.execute(
                """
                UPDATE buckets
                SET balance_cents = ?
                WHERE bucket_code = 'BUCKET_HARDSHIP_RESERVE'
                """,
                (new_reserve_cents,),
            )

        payload = {
            "member_token": member_token,
            "status": "MEMBER_RETIRED",
            "mode": mode,
            "requested_cents": int(requested_cents),
            "granted_cents": granted_cents,
            "uncovered_cents": uncovered_cents,
            "retiree_hardship_cap_cents": cap_cents,
            "retiree_hardship_used_cents": new_used_cents,
            "remaining_retiree_cap_cents": max(0, cap_cents - new_used_cents),
            "min_reserve_floor_cents": int(min_reserve_floor_cents),
            "hardship_reserve_after_cents": new_reserve_cents,
        }

        if hasattr(self, "_append_event"):
            self._append_event(
                rule_code="RETIREE_01_HARDSHIP_CLAIM",
                entity_type="MEMBER",
                entity_id=member_token,
                from_state="MEMBER_RETIRED",
                to_state="MEMBER_RETIRED",
                bucket_code="BUCKET_HARDSHIP_RESERVE",
                delta_cents=-granted_cents,
                payload=payload,
            )
        else:
            self.conn.commit()

        return payload

    CoopBankLedger._ensure_retiree_table = _ensure_retiree_table
    CoopBankLedger.retire_member = retire_member
    CoopBankLedger.claim_retiree_hardship = claim_retiree_hardship

    def distribute_retiree_interest(
        self: CoopBankLedger,
        gross_yield_cents: int,
        retiree_yield_share: float = 0.25,
        equalization_alpha: float = 2.0,
        recent_contributions_by_member: Dict[str, int] | None = None,
        min_reserve_floor_cents: int = 0,
        source_bucket: str = "BUCKET_HARDSHIP_RESERVE",
        mode: str = "cap",
    ) -> Dict[str, Any]:
        if gross_yield_cents < 0:
            raise ValueError("gross_yield_cents must be non-negative")
        if not (0.0 <= float(retiree_yield_share) <= 1.0):
            raise ValueError("retiree_yield_share must be between 0.0 and 1.0")
        if float(equalization_alpha) < 0.0:
            raise ValueError("equalization_alpha must be non-negative")
        if int(min_reserve_floor_cents) < 0:
            raise ValueError("min_reserve_floor_cents must be non-negative")
        if mode not in ("reject", "cap"):
            raise ValueError("mode must be 'reject' or 'cap'")

        self._ensure_retiree_table()
        retirees = self.conn.execute(
            """
            SELECT member_token, tenure_months
            FROM retiree_members
            WHERE status = 'MEMBER_RETIRED'
            ORDER BY member_token ASC
            """
        ).fetchall()

        contrib_map = recent_contributions_by_member or {}
        for k, v in contrib_map.items():
            if int(v) < 0:
                raise ValueError(f"Contribution count for {k} cannot be negative")

        target_pool_cents = int((int(gross_yield_cents) * float(retiree_yield_share)) // 1)
        bucket_row = self.conn.execute(
            "SELECT balance_cents FROM buckets WHERE bucket_code = ?",
            (source_bucket,),
        ).fetchone()
        reserve_cents = int(bucket_row["balance_cents"]) if bucket_row else 0
        spendable_reserve = max(0, reserve_cents - int(min_reserve_floor_cents))

        if mode == "reject" and target_pool_cents > spendable_reserve:
            raise ValueError(
                f"Retiree interest pool shortfall: target={target_pool_cents}c > spendable={spendable_reserve}c"
            )

        pool_cents = target_pool_cents if mode == "reject" else min(target_pool_cents, spendable_reserve)
        if not retirees or pool_cents <= 0:
            payouts = [
                {
                    "member_token": r["member_token"],
                    "tenure_months": int(r["tenure_months"]),
                    "recent_contribution_count": int(contrib_map.get(r["member_token"], 0)),
                    "equalizer_weight": 0.0,
                    "payout_cents": 0,
                }
                for r in retirees
            ]
            payload = {
                "gross_yield_cents": int(gross_yield_cents),
                "retiree_yield_share": float(retiree_yield_share),
                "equalization_alpha": float(equalization_alpha),
                "target_pool_cents": target_pool_cents,
                "disbursed_total_cents": 0,
                "undistributed_cents": target_pool_cents,
                "source_bucket": source_bucket,
                "reserve_after_cents": reserve_cents,
                "payouts": payouts,
            }
            if hasattr(self, "_append_event"):
                self._append_event(
                    rule_code="RETIREE_02_INTEREST_EQUALIZATION",
                    entity_type="RETIREE_POOL",
                    entity_id=source_bucket,
                    from_state="YIELD_CALCULATED",
                    to_state="NO_DISTRIBUTION",
                    bucket_code=source_bucket,
                    delta_cents=0,
                    payload=payload,
                )
            else:
                self.conn.commit()
            return payload

        alpha = float(equalization_alpha)
        weights: list[tuple[str, int, int, float]] = []
        total_weight = 0.0
        for r in retirees:
            token = r["member_token"]
            tenure = max(1, int(r["tenure_months"]))
            c_count = int(contrib_map.get(token, 0))
            eq_multiplier = (1.0 + alpha) / (1.0 + alpha * c_count)
            w = float(tenure) * eq_multiplier
            weights.append((token, tenure, c_count, w))
            total_weight += w

        exact_shares: list[tuple[str, int, int, float, int, float]] = []
        floor_sum = 0
        for token, tenure, c_count, w in weights:
            exact = (float(pool_cents) * w) / total_weight
            base_cents = int(exact // 1)
            fractional = exact - base_cents
            exact_shares.append((token, tenure, c_count, w, base_cents, fractional))
            floor_sum += base_cents

        remainder = int(pool_cents - floor_sum)
        # Deterministic remainder allocation: highest fractional part, tie-broken by member_token ascending
        ranked_indices = sorted(
            range(len(exact_shares)),
            key=lambda idx: (-exact_shares[idx][5], exact_shares[idx][0]),
        )
        bonus_map = {idx: 0 for idx in range(len(exact_shares))}
        for idx in ranked_indices[:remainder]:
            bonus_map[idx] = 1

        payouts_list: list[Dict[str, Any]] = []
        disbursed_sum = 0
        for idx, (token, tenure, c_count, w, base_cents, _) in enumerate(exact_shares):
            final_cents = base_cents + bonus_map[idx]
            disbursed_sum += final_cents
            payouts_list.append(
                {
                    "member_token": token,
                    "tenure_months": tenure,
                    "recent_contribution_count": c_count,
                    "equalizer_weight": round(w, 6),
                    "payout_cents": final_cents,
                }
            )

        new_reserve_cents = reserve_cents - disbursed_sum
        if bucket_row and disbursed_sum > 0:
            self.conn.execute(
                "UPDATE buckets SET balance_cents = ? WHERE bucket_code = ?",
                (new_reserve_cents, source_bucket),
            )

        payload = {
            "gross_yield_cents": int(gross_yield_cents),
            "retiree_yield_share": float(retiree_yield_share),
            "equalization_alpha": alpha,
            "target_pool_cents": target_pool_cents,
            "disbursed_total_cents": disbursed_sum,
            "undistributed_cents": max(0, target_pool_cents - disbursed_sum),
            "source_bucket": source_bucket,
            "reserve_after_cents": new_reserve_cents,
            "payouts": payouts_list,
        }

        if hasattr(self, "_append_event"):
            self._append_event(
                rule_code="RETIREE_02_INTEREST_EQUALIZATION",
                entity_type="RETIREE_POOL",
                entity_id=source_bucket,
                from_state="YIELD_CALCULATED",
                to_state="EQUALIZED_DISBURSED",
                bucket_code=source_bucket,
                delta_cents=-disbursed_sum,
                payload=payload,
            )
        else:
            self.conn.commit()

        return payload

    CoopBankLedger.distribute_retiree_interest = distribute_retiree_interest

    def audit_ledger_as_member(
        self: CoopBankLedger,
        auditor_token: str,
        record_audit_event: bool = True,
    ) -> Dict[str, Any]:
        assert auditor_token.startswith("[Member_"), f"Invalid auditor token: {auditor_token}"

        retiree_table = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='retiree_members'"
        ).fetchone()
        retiree_row = None
        if retiree_table:
            retiree_row = self.conn.execute(
                "SELECT member_token, status, tenure_months, retiree_hardship_cap_cents, retiree_hardship_used_cents FROM retiree_members WHERE member_token = ?",
                (auditor_token,),
            ).fetchone()

        is_retired = bool(retiree_row and retiree_row["status"] == "MEMBER_RETIRED")
        auditor_role = "AUDITOR_MEMBER_RETIRED" if is_retired else "AUDITOR_ACTIVE_MEMBER"

        events = self.conn.execute(
            "SELECT * FROM ledger_events ORDER BY event_id ASC"
        ).fetchall()

        chain_valid = True
        broken_at_event_id: int | None = None
        if hasattr(self, "verify_chain"):
            chain_res = self.verify_chain()
            if isinstance(chain_res, dict):
                chain_valid = bool(chain_res.get("valid", chain_res.get("intact", True)))
                broken_at_event_id = chain_res.get("broken_at_event_id")
            else:
                chain_valid = bool(chain_res)

        expected_prev_hash = "GENESIS_" + ("0" * 56)
        head_hash = expected_prev_hash
        recomputed_bucket_deltas: Dict[str, int] = {}

        for idx, ev in enumerate(events):
            ev_dict = dict(ev)
            prev_hash = ev_dict.get("prev_hash", "")
            stored_hash = ev_dict.get("event_hash", "")
            if idx == 0 and prev_hash not in (expected_prev_hash, "GENESIS"):
                chain_valid = False
                broken_at_event_id = int(ev_dict.get("event_id", 0))
            elif idx > 0 and prev_hash != expected_prev_hash:
                chain_valid = False
                broken_at_event_id = int(ev_dict.get("event_id", 0))
            if stored_hash:
                expected_prev_hash = stored_hash
                head_hash = stored_hash

            b_code = ev_dict.get("bucket_code")
            d_cents = int(ev_dict.get("delta_cents") or 0)
            if b_code:
                recomputed_bucket_deltas[b_code] = recomputed_bucket_deltas.get(b_code, 0) + d_cents

        buckets_rows = self.conn.execute(
            "SELECT bucket_code, balance_cents FROM buckets ORDER BY bucket_code ASC"
        ).fetchall()
        current_buckets = {r["bucket_code"]: int(r["balance_cents"]) for r in buckets_rows}

        retiree_count = 0
        if retiree_table:
            retiree_count = int(
                self.conn.execute(
                    "SELECT COUNT(*) AS cnt FROM retiree_members WHERE status = 'MEMBER_RETIRED'"
                ).fetchone()["cnt"]
            )

        mortgage_count = 0
        mortgages_table = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mortgages'"
        ).fetchone()
        if mortgages_table:
            mortgage_count = int(
                self.conn.execute("SELECT COUNT(*) AS cnt FROM mortgages").fetchone()["cnt"]
            )

        iso_status = self.verify_iso12207_compliance() if hasattr(self, "verify_iso12207_compliance") else None

        audit_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "auditor_token": auditor_token,
                    "auditor_role": auditor_role,
                    "event_count": len(events),
                    "head_hash": head_hash,
                    "current_buckets": current_buckets,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        report = {
            "rule_code": "AUDIT_01_UNIVERSAL_LEDGER_AUDIT",
            "auditor_token": auditor_token,
            "auditor_role": auditor_role,
            "can_retiree_audit": True,
            "chain_intact": chain_valid,
            "broken_at_event_id": broken_at_event_id,
            "iso12207_compliant": iso_status.get("compliant") if isinstance(iso_status, dict) else None,
            "event_count": len(events),
            "head_hash": head_hash,
            "buckets_balance_cents": current_buckets,
            "event_bucket_deltas_cents": recomputed_bucket_deltas,
            "retired_members_count": retiree_count,
            "mortgages_count": mortgage_count,
            "audit_receipt_sha256": audit_fingerprint,
        }

        if record_audit_event and hasattr(self, "_append_event"):
            self._append_event(
                rule_code="AUDIT_01_UNIVERSAL_LEDGER_AUDIT",
                entity_type="MEMBER_AUDIT",
                entity_id=auditor_token,
                from_state="LEDGER_STATE",
                to_state="AUDIT_VERIFIED" if chain_valid else "AUDIT_CHAIN_BROKEN",
                bucket_code=None,
                delta_cents=0,
                payload=report,
            )

        return report
    CoopBankLedger.audit_ledger_as_member = audit_ledger_as_member

    def evaluate_evidence_completeness(
        self: CoopBankLedger,
        case_id: str,
        member_token: str,
        identified_witnesses: int,
        verified_witness_statements: int,
        member_rebuttal_recorded: bool,
        defense_reconciled: bool,
        employer_documents_only: bool = False,
        min_completeness_threshold: float = 0.75,
    ) -> Dict[str, Any]:
        assert member_token.startswith("[Member_"), f"Invalid member token: {member_token}"
        if identified_witnesses < 0 or verified_witness_statements < 0:
            raise ValueError("Witness counts must be non-negative")
        if verified_witness_statements > identified_witnesses:
            raise ValueError("Verified witness statements cannot exceed identified witnesses")
        if not (0.0 <= float(min_completeness_threshold) <= 1.0):
            raise ValueError("min_completeness_threshold must be between 0.0 and 1.0")

        if identified_witnesses > 0:
            witness_ratio = round(verified_witness_statements / identified_witnesses, 6)
            witness_gap = verified_witness_statements < identified_witnesses
        else:
            witness_ratio = 1.0
            witness_gap = False

        rebuttal_gap = not bool(member_rebuttal_recorded)
        reconciliation_gap = not bool(defense_reconciled)
        employer_only_gap = bool(employer_documents_only and (witness_gap or rebuttal_gap))

        raw_score = (
            0.40 * witness_ratio
            + 0.35 * (1.0 if member_rebuttal_recorded else 0.0)
            + 0.25 * (1.0 if defense_reconciled else 0.0)
        )
        if employer_only_gap:
            raw_score = min(raw_score, 0.40)
        completeness_score = round(raw_score, 6)

        has_deficit = (
            completeness_score < float(min_completeness_threshold)
            or witness_gap
            or rebuttal_gap
            or employer_only_gap
        )
        verdict = "EVIDENCE_COMPLETE" if not has_deficit else "ADVOCACY_EVIDENCE_DEFICIT"

        flags: list[str] = []
        if witness_gap:
            flags.append("WITNESS_VERIFICATION_INCOMPLETE")
        if rebuttal_gap:
            flags.append("MEMBER_REBUTTAL_MISSING")
        if reconciliation_gap:
            flags.append("DEFENSE_SUMMARY_UNRECONCILED")
        if employer_only_gap:
            flags.append("EMPLOYER_RECORD_UNCORROBORATED")
        if not flags:
            flags.append("FULL_WITNESS_AND_REBUTTAL_RECORDED")

        payload = {
            "case_id": case_id,
            "member_token": member_token,
            "verdict": verdict,
            "completeness_score": completeness_score,
            "min_completeness_threshold": float(min_completeness_threshold),
            "identified_witnesses": int(identified_witnesses),
            "verified_witness_statements": int(verified_witness_statements),
            "witness_verified_ratio": witness_ratio,
            "member_rebuttal_recorded": bool(member_rebuttal_recorded),
            "defense_reconciled": bool(defense_reconciled),
            "employer_documents_only": bool(employer_documents_only),
            "flags": flags,
        }

        if hasattr(self, "_append_event"):
            prev_row = self.conn.execute(
                """
                SELECT to_state FROM ledger_events
                WHERE rule_code = 'ADVOCACY_01_EVIDENCE_COMPLETENESS' AND entity_id = ?
                ORDER BY event_id DESC LIMIT 1
                """,
                (case_id,),
            ).fetchone()
            from_state = prev_row["to_state"] if prev_row and prev_row["to_state"] else "UNREVIEWED"
            self._append_event(
                rule_code="ADVOCACY_01_EVIDENCE_COMPLETENESS",
                entity_type="GRIEVANCE_CASE",
                entity_id=case_id,
                from_state=from_state,
                to_state=verdict,
                bucket_code=None,
                delta_cents=0,
                payload=payload,
            )
        else:
            self.conn.commit()

        return payload

    CoopBankLedger.evaluate_evidence_completeness = evaluate_evidence_completeness


_attach_mortgage_strike_deferral_rule()

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["reject", "cap"], default="cap")
    p.add_argument("--test", choices=["all", "stress", "tamper"], default="all")
    p.add_argument("--export-bundle", default=None, help="Write SHA-256 verified JSON audit bundle to path")
    p.add_argument("--check-iso12207", action="store_true", help="Include ISO/IEC 12207 compliance check in output")
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
        if getattr(args, "check_iso12207", False):
            out["iso12207_compliance"] = db.verify_iso12207_compliance(args.export_bundle)
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
