# Cooperative Bank Ledger Extension (`coop_bank_ledger`)

Zero-dependency, local-first SQLite ledger prototype with SHA-256 hash chaining, ring-fenced bucket allocation, deterministic strike-necessity governance, economic balance checks, worker mortgage strike-deferral/resumption rules, post-retirement membership & capped hardship rules, equalized retiree interest disbursement, advocacy evidence/witness completeness checks, universal member/retiree ledger auditing, and ISO/IEC 12207 lifecycle verification.

## Bucket Allocation
* **Lending Pool (`BUCKET_LENDING_POOL`)**: `55%` (`0.55`)
* **Legal Defense (`BUCKET_LEGAL_DEFENSE`)**: `25%` (`0.25`)
* **Hardship Reserve (`BUCKET_HARDSHIP_RESERVE`)**: `20%` (`0.20`)

## CLI Usage
~~~bash
python3 coop_bank_ledger_prototype.py --mode reject|cap --test all|stress|tamper [--check-iso12207] [--export-bundle path/to/bundle.json]
~~~

## Deterministic Governance (`GOVERNANCE_01_STRIKE_NECESSITY`)
`CoopBankLedger.evaluate_strike_necessity()` evaluates objective workplace, economic balance, and ledger liquidity signals, then appends the verdict (`STRIKE_NOT_NECESSARY`, `MEDIATION_ALERT`, `MEDIATION_OVER_DEMAND_CHECK`, `STRIKE_DEFERRED_INSUFFICIENT_RESERVE`, or `STRIKE_NECESSARY`) and `balance_flag` to the SHA-256 event chain:
* **Procedural Breach Rate**: `invalid_disciplinary_signings / total_disciplinary_signings` (greater than `15%` default threshold)
* **Unresolved Grievance Rate**: `unresolved_grievances / total_grievances` (greater than `10%` default threshold)
* **Safety Deficit Rate**: `unresolved_safety_hazards / total_safety_checks` (greater than `10%` default threshold)
* **Bargaining Impasse**: `contract_expired`, `mediation_exhausted`, and `real_wage_gap_pct` greater than `3%` default threshold
* **Liquidity Readiness**: `BUCKET_HARDSHIP_RESERVE + BUCKET_LENDING_POOL` greater than or equal to `min_strike_runway_cents`

## Economic & Safety Balance Flags
* **`PROFIT_OVER_SAFETY`**: Employer operating margin is healthy (`employer_operating_margin_pct >= 10%`) while workplace safety/fairness deficit is elevated (`safety_deficit_rate > 10%` or `procedural_breach_rate > 15%`).
* **`LOW_MARGIN_OVER_DEMAND`**: Employer operating margin is strained (`employer_operating_margin_pct < 3%`) while wage/financial demand gap exceeds the strained cap (`real_wage_gap_pct > 4%`).
* **`CONFLICTED_EXTREME`**: Both `PROFIT_OVER_SAFETY` and `LOW_MARGIN_OVER_DEMAND` conditions occur simultaneously.
* **`BALANCED_EQUILIBRIUM`**: Neither economic imbalance flag is triggered.

## Worker Mortgages & Strike Deferral (`MORTGAGE_00` – `MORTGAGE_02`)
* **`create_mortgage()` (`MORTGAGE_00_ORIGINATED`)**: Registers a worker mortgage linked to a member token and bargaining unit in `MORTGAGE_ACTIVE` state.
* **`apply_strike_mortgage_deferral()` (`MORTGAGE_01_STRIKE_DEFERRAL`)**: Requires a `STRIKE_NECESSARY` verdict for the unit, enforces `max_deferral_months` and `min_reserve_floor_cents` above `BUCKET_HARDSHIP_RESERVE`, supports `mode="reject"` or `mode="cap"`, and transitions to `STRIKE_DEFERRED` or `STRIKE_DEFERRED_PARTIAL`.
* **`resume_mortgage_after_strike()` (`MORTGAGE_02_POST_STRIKE_RESUME`)**: Transitions deferred mortgages back to `MORTGAGE_ACTIVE` with `append_term` or `recalculate_installment` repayment metadata logged in the SHA-256 chain.

## Post-Retirement Membership, Capped Hardship & Interest Equalization (`MEMBER_01`, `RETIREE_01` – `RETIREE_02`)
* **`retire_member()` (`MEMBER_01_RETIREMENT_TRANSITION`)**: Transitions eligible members (`tenure_months >= min_retirement_tenure_months`) into `MEMBER_RETIRED`, enables mortgage protection against retirement-triggered recall/acceleration, and assigns a deterministic `retiree_hardship_cap_cents` allowance.
* **`claim_retiree_hardship()` (`RETIREE_01_HARDSHIP_CLAIM`)**: Draws capped hardship relief from `BUCKET_HARDSHIP_RESERVE` subject to remaining member retiree cap and `min_reserve_floor_cents` (`mode="reject"` or `mode="cap"`).
* **`distribute_retiree_interest()` (`RETIREE_02_INTEREST_EQUALIZATION`)**: Disburses yield in exact integer cents using tenure and an inverse-contribution equalizer multiplier `(1 + alpha) / (1 + alpha * recent_contribution_count)` so retirees with lower recent contribution counts receive balanced equalization rather than pure balance concentration.

## Advocacy Evidence & Universal Member Audit (`ADVOCACY_01_EVIDENCE_COMPLETENESS`, `AUDIT_01_UNIVERSAL_LEDGER_AUDIT`)
* **`evaluate_evidence_completeness()`**: Computes a weighted `completeness_score` (`0.0` to `1.0`) across verified witness statements, recorded member rebuttals, defense-summary reconciliation, and uncorroborated employer-only records, appending `EVIDENCE_COMPLETE` or `ADVOCACY_EVIDENCE_DEFICIT` to the SHA-256 event chain.
* **`audit_ledger_as_member()`**: Allows any active member (`AUDITOR_ACTIVE_MEMBER`) or retired member (`AUDITOR_MEMBER_RETIRED`) to independently verify hash-chain linkage, bucket balances, and ledger counts with a cryptographic audit receipt.

## Integrity & Compliance Checks
* **Hash Chain & Bundle Digest**: Verifies `prev_hash` / `event_hash` links across `ledger_events` and recomputes `bundle_sha256` over the canonical JSON body.
* **Bucket Reconciliation (`--check-iso12207`)**: Reconciles `buckets.balance_cents` against cash-movement deltas and checks bucket share governance and event traceability.
