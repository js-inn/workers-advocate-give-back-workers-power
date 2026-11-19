# Workers Advocate: Give Back Workers' Power

A local-first, zero-dependency Python and SQLite cryptographic auditing engine designed to dismantle the illusion of union protection, track institutional and administrative delays (such as protracted Duty of Fair Representation board backlogs), and restore financial and legal sovereignty to individual workers.

## Core Principles
* **Individual Sovereignty:** Workers retain absolute control over their data, grievance histories, and legal timelines via local-first SQLite ledgers (`core_audit.db`).
* **Cryptographic Provenance:** Every grievance and institutional stage transition is secured using sequential SHA-256 hashing to ensure tamper-proof non-repudiation.
* **Demolishing Fabricated Fear:** Replacing opaque union discretion and bureaucratic gatekeeping with transparent, verifiable compliance reports ready for community legal review.

## Architecture
* **Local-First Execution:** Runs entirely on mobile/local hardware via Python standard libraries (`sqlite3`, `hashlib`, `json`, `datetime`), removing dependency on third-party cloud infrastructure.
* **Immutable Audit Trail:** Generates verifiable JSON compliance reports (`audit_compliance_report.json`) for independent legal consultation.
