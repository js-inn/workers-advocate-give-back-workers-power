#!/usr/bin/env python3
"""End-to-end verification harness for CoopBankLedger rules."""

import json
from coop_bank_ledger_prototype import CoopBankLedger


def main() -> None:
    db = CoopBankLedger(":memory:")

    # 1. Enroll members & post dues into 55/25/20 ring-fenced buckets
    for i in range(1, 26):
        token = f"[Member_{i:04d}]"
        db.enroll_member(token, 1)
        db.post_dues(token, 1_000_000, 1)

    unit = "[Unit_Public_Service]"

    # 2. Strike necessity evaluation (GOVERNANCE_01_STRIKE_NECESSITY)
    strike = db.evaluate_strike_necessity(
        unit_id=unit,
        total_disciplinary_signings=20,
        invalid_disciplinary_signings=5,
        total_grievances=20,
        unresolved_grievances=4,
        contract_expired=True,
        mediation_exhausted=True,
        real_wage_gap_pct=0.055,
        min_strike_runway_cents=5_000_000,
        employer_operating_margin_pct=0.14,
        total_safety_checks=20,
        unresolved_safety_hazards=4,
    )
    assert strike["verdict"] == "STRIKE_NECESSARY", f"Unexpected strike verdict: {strike}"
    assert strike["balance_flag"] == "PROFIT_OVER_SAFETY"

    # 3. Worker mortgage origination, strike deferral & post-strike resumption
    db.create_mortgage(
        mortgage_id="MTG_001",
        member_token="[Member_0002]",
        unit_id=unit,
        principal_cents=28_000_000,
        monthly_payment_cents=1_620_000,
    )
    deferral = db.apply_strike_mortgage_deferral(
        mortgage_id="MTG_001",
        months_requested=3,
        max_deferral_months=6,
        min_reserve_floor_cents=2_000_000,
        mode="cap",
    )
    assert deferral["status"] in ("STRIKE_DEFERRED", "STRIKE_DEFERRED_PARTIAL")

    resumed = db.resume_mortgage_after_strike(
        mortgage_id="MTG_001",
        repayment_mode="append_term",
        repayment_months=3,
    )
    assert resumed["status"] == "MORTGAGE_ACTIVE"

    # 4. Retiree transition, capped hardship & inverse-contribution interest equalization
    retired = db.retire_member(
        member_token="[Member_0001]",
        tenure_months=160,
        min_retirement_tenure_months=60,
        retiree_hardship_cap_cents=600_000,
    )
    db.retire_member(
        member_token="[Member_0003]",
        tenure_months=160,
        min_retirement_tenure_months=60,
        retiree_hardship_cap_cents=600_000,
    )
    assert retired["status"] == "MEMBER_RETIRED"

    hardship = db.claim_retiree_hardship(
        member_token="[Member_0001]",
        requested_cents=900_000,
        min_reserve_floor_cents=2_000_000,
        mode="cap",
    )
    assert hardship["granted_cents"] == 600_000

    interest = db.distribute_retiree_interest(
        gross_yield_cents=6_000_000,
        retiree_yield_share=0.25,
        equalization_alpha=2.0,
        recent_contributions_by_member={
            "[Member_0001]": 0,
            "[Member_0003]": 5,
        },
        min_reserve_floor_cents=1_000_000,
        source_bucket="BUCKET_HARDSHIP_RESERVE",
        mode="cap",
    )
    assert sum(p["payout_cents"] for p in interest["payouts"]) == interest["disbursed_total_cents"]
    assert interest["payouts"][0]["payout_cents"] > interest["payouts"][1]["payout_cents"]

    # 5. Evidence completeness (ADVOCACY_01_EVIDENCE_COMPLETENESS)
    evidence = db.evaluate_evidence_completeness(
        case_id="[Case_101]",
        member_token="[Member_0004]",
        identified_witnesses=3,
        verified_witness_statements=3,
        member_rebuttal_recorded=True,
        defense_reconciled=True,
        employer_documents_only=False,
    )
    assert evidence["verdict"] == "EVIDENCE_COMPLETE"
    assert evidence["completeness_score"] == 1.0

    # 6. Internal membership grievance filing (MEMBER_GRIEVANCE_01_FILING)
    grievance = db.file_membership_grievance(
        grievance_id="[MemGriev_101]",
        member_token="[Member_0001]",
        category="RETIREE_PRIVILEGE",
        summary="Verify retiree yield equalization math and reserve ceiling enforcement.",
        identified_witnesses=2,
        verified_witnesses=2,
        member_rebuttal_recorded=True,
        defense_reconciled=True,
    )
    assert grievance["member_standing"] == "MEMBER_RETIRED"
    assert grievance["evidence_verdict"] == "EVIDENCE_COMPLETE"

    # 7. Anti-hoarding surplus cap (GOVERNANCE_02_ANTI_HOARDING_CAP)
    hoarding = db.evaluate_anti_hoarding_cap(
        max_reserve_ceiling_cents=15_000_000,
        min_reserve_floor_cents=3_000_000,
        surplus_return_share=0.50,
        source_bucket="BUCKET_LENDING_POOL",
        mode="cap",
    )
    assert hoarding["anti_hoarding_flag"] == "SURPLUS_RETURN_REQUIRED"
    assert hoarding["surplus_return_pool_cents"] > 0

    # 8. Universal member & retiree audit + upgraded ISO/IEC 12207 compliance check
    retiree_audit = db.audit_ledger_as_member("[Member_0001]")
    active_audit = db.audit_ledger_as_member("[Member_0002]")
    iso = db.verify_iso12207_compliance()

    assert retiree_audit["auditor_role"] == "AUDITOR_MEMBER_RETIRED"
    assert active_audit["auditor_role"] == "AUDITOR_ACTIVE_MEMBER"
    assert retiree_audit["chain_intact"] is True
    assert active_audit["chain_intact"] is True
    assert iso["compliant"] is True, f"ISO check failed: {iso}"

    print(
        json.dumps(
            {
                "status": "ALL_CHECKS_PASSED",
                "strike_verdict": strike["verdict"],
                "mortgage_status": resumed["status"],
                "retiree_hardship_granted_cents": hardship["granted_cents"],
                "retiree_interest_disbursed_cents": interest["disbursed_total_cents"],
                "evidence_score": evidence["completeness_score"],
                "grievance_status": grievance["status"],
                "surplus_return_pool_cents": hoarding["surplus_return_pool_cents"],
                "retiree_audit_receipt": retiree_audit["audit_receipt_sha256"][:16],
                "iso12207_gates": iso["gates"],
                "iso12207_compliant": iso["compliant"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
