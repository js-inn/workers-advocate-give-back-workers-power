# Statement of Applicability (SoA) — ISO/IEC 27001:2022

- **System / Scope**: `workers-advocate-give-back-workers-power` local-first audit, validation, and bundle verification pipeline (`~/crypto_tools`)
- **Owner / Operator**: Jujita Fermin Stairs / JS innovation8390-1 inc. (Edmonton, Alberta, Canada)
- **Standard Reference**: ISO/IEC 27001:2022 (Annex A Controls)
- **Effective Date**: November 18, 2026
- **Cryptographic Scope Note**: SHA-256 hashes (`evidence_hash`, `action_hash`, `validation_hash`, `result_hash`, `simulation_hash`, and `bundle_sha256`) provide **deterministic integrity and tamper-evidence** over canonicalized JSON/record data—they do **not** provide confidentiality or encryption at rest. Local SQLite files (`*.db`) and generated export bundles (`exports/`, `workers_advocate_audit_bundle.json`) are excluded from version control via `.gitignore` and stored locally.

## Annex A Control Mapping

| Control ID | Annex A Control Title | Applicability | Implementation Status | Pipeline Mapping & Operational Justification |
| :--- | :--- | :--- | :--- | :--- |
| **A.5.1** | Policies for information security | Applicable | Documented | Governed by repository hygiene rules (`README.md`), privacy/tokenization standards, and this Statement of Applicability. |
| **A.5.12** | Classification of information | Applicable | Implemented | Records and artifacts are separated into public source/docs in Git, anonymized worker tokens (`[Worker_001]`, `[Worker_002]`, `[Union_Local_A]`), and local-only SQLite databases/exports (`*.db`, `exports/`). |
| **A.5.13** | Labelling of information | Applicable | Implemented | Worker and union entities use explicit bracketed anonymization tokens (`[Worker_001]`, `[Worker_002]`, `[Union_Local_A]`), dates use ISO format (`YYYY-MM-DD`), and bundles carry explicit `bundle_sha256` fields. |
| **A.5.15** | Access control | Not Applicable | Excluded | No multi-user application server, role-based access control (RBAC) layer, or shared database service exists in the local-first CLI pipeline; host OS/device user permissions govern local files. |
| **A.5.23** | Information security for use of cloud services | Not Applicable | Excluded | The audit pipeline is local-first with zero third-party cloud database or hosted backend dependencies; GitHub is used solely for source/documentation version control with PII-free artifacts. |
| **A.5.24** | Information security incident management planning and preparation | Applicable | Documented | If a hash mismatch (`MISMATCH`) or accidental PII pattern match occurs during `iso27001_audit_check.py` or `cohort_audit_runner.py`, halt bundle publication, isolate affected records, re-verify source inputs, and regenerate clean hashes. |
| **A.5.28** | Collection of evidence | Applicable | Implemented | Scanners and validators (`workplace_inconsistency_scanner.py`, `union_action_validator.py`, `dfr_fairness_validator.py`, `alrb_dfr_simulation.py`) compute deterministic SHA-256 hashes, and `export_combined_audit_bundle.py` / `cohort_audit_runner.py` seal and verify `bundle_sha256`. |
| **A.5.30** | ICT readiness for business continuity | Applicable | Partially Implemented | Local-first execution keeps the pipeline independent of external cloud outages; full resilience requires periodic offline/local backups of `core_audit_sanitized.db` and `alrb_dfr_sim.db` outside the working directory. |
| **A.5.33** | Protection of records | Applicable | Implemented | Audit outputs are stored in structured SQLite tables and exported JSON bundles (`exports/audit_bundle_Worker_001.json`, `exports/audit_bundle_Worker_002.json`); `.gitignore` prevents accidental commit of local `.db` files and export bundles. |
| **A.5.34** | Privacy and protection of PII | Applicable | Implemented | Pipeline enforces anonymized tokens rather than personal identifiers, and `iso27001_audit_check.py` scans repository files for email, phone, and dashed national ID/SIN/SSN patterns. |
| **A.5.37** | Documented operating procedures | Applicable | Implemented | `README.md` and `iso27001_audit_check.py` document and automate standard execution, read-only inspection, cohort bundle export, and integrity verification. |
| **A.8.1** | User endpoint devices | Not Applicable | Excluded | Hardware/device security (screen locks, OS patching, device encryption) is managed at the endpoint/host environment level rather than within the Python audit scripts. |
| **A.8.9** | Configuration management | Applicable | Implemented | Deterministic CLI flags (`--worker-id`, `--output`, `--workers`, `--output-dir`, `--skip-readonly`), `.gitignore` rules, and version-controlled scripts maintain repeatable pipeline configuration. |
| **A.8.11** | Data masking | Applicable | Implemented | Personal/entity identifiers in audit workflows are masked/pseudonymized via standardized tokens (`[Worker_001]`, `[Worker_002]`, `[Union_Local_A]`) and sanitized summaries. |
| **A.8.12** | Data leakage prevention | Applicable | Implemented | `.gitignore` blocks `*.db`, SQLite journal/WAL/SHM files, and `exports/` bundles from Git commits, while `iso27001_audit_check.py` checks for PII patterns prior to release/sharing. |
| **A.8.24** | Use of cryptography | Applicable | Implemented | Uses Python's standard `hashlib.sha256` over canonical JSON (`json.dumps(..., sort_keys=True, ensure_ascii=False)`) to produce verifiable integrity digests (`bundle_sha256` and record hashes). |
| **A.8.28** | Secure coding | Applicable | Implemented | Uses parameterized SQL queries, explicit read-only SQLite URI mode (`?mode=ro`) in inspection scripts, and standard library modules without external runtime dependencies. |
| **A.8.32** | Change management | Applicable | Implemented | All code, documentation, and verification scripts are tracked in Git on `main` (`js-inn/workers-advocate-give-back-workers-power`), and `iso27001_audit_check.py` verifies a clean working tree. |
| **A.8.34** | Protection during audit testing | Applicable | Implemented | `audit_worker_readonly.py` opens `core_audit_sanitized.db` and `alrb_dfr_sim.db` in read-only mode (`?mode=ro`), preventing accidental mutation during inspection. |
