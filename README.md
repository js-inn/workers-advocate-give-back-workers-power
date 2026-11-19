# Workers Advocate: Give Back Workers' Power

Local-first, zero-third-party-cloud audit and verification pipeline for workplace inconsistency flags, union representation timeliness, Duty of Fair Representation (DFR) criteria, and Alberta Labour Relations Board (ALRB) procedural checks using deterministic **SHA-256** hashes and anonymized tokens (`[Worker_002]`, `[Union_Local_A]`).

## Modules

- `workplace_inconsistency_scanner.py`: Evaluates complaints, hardship/requests, and disciplinary records against `RULE_01`–`RULE_04` with SHA-256 hashes in `core_audit_sanitized.db`.
- `union_action_validator.py`: Tracks union actions, `days_elapsed`, and validation flags with SHA-256 hashes.
- `dfr_fairness_validator.py`: Evaluates `DFR_01`–`DFR_03` rules against union actions and high/critical flag counts.
- `alrb_dfr_simulation.py`: Runs ALRB scenario checks (`Case_A`, `Case_B`, `Case_C`) in `alrb_dfr_sim.db` with simulation hashes.
- `export_combined_audit_bundle.py`: Exports a worker-scoped JSON bundle (`--worker-id`, `--output`) sealed with `bundle_sha256`.
- `audit_worker_readonly.py`: Inspects database rows and hashes in read-only mode (`?mode=ro`).
- `cohort_audit_runner.py`: Runs read-only checks and exports verified per-worker JSON bundles into `exports/`.

## License & Attribution

Licensed under the **Creator Attribution and Sublicense License** — see [LICENSE](LICENSE) for first attribution to **Jujita Fermin Stairs / JS innovation8390-1 inc.** and creator sublicense rights.
