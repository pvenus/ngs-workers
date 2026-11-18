# Resident service candidate validation

Validated on 2026-11-18 in the isolated `dev/resident-services-20261118` worktree.

- **230 tests passed, zero skipped**: 191 resident/service tests plus 39 media/full-frame tests, each top-level media test file isolated to prevent old/candidate PDS module-cache contamination.
- Source contracts: NGS `runtime-space-0597802`, existing PDS `backend-f3e61b1`, media PDS candidate `music-pds-publish-20261118`.
- Test interpreter: explicitly selected Python environment with Pillow. No dependency installation was required.
- `git diff --check` passed. No operating database, original content, service, launchd entry or credential was changed.

Coverage includes canonical claims, response-loss idempotency, fence/owner checks, original intake-pinned guards, atomic publication reservations, server-verified receipt checkpoints before cleanup, full-frame extraction and mandatory half-size pixel processing, temporary ownership/cleanup, model memory admission, independent worker composition, single-candidate parent auto-apply boundaries, and automatic evidence-based reconciliation.

The last recovery regression deletes temporary evidence and expires the reconciliation owner during cleanup. A subsequent tick completes from the previously persisted verified proof without generation or missing-scratch reads.

Native CLI/model/provider calls use synthetic executables or synthetic model outputs in tests. Real ffmpeg extraction and the actual PDS/Filex publication code are exercised against temporary fixtures. **This is not real image/video/music output acceptance or an operating cutover.** Inactive deployment templates still require the real reviewed site bindings, service credentials, model paths/budgets, trusted stop/unload proofs, and explicit old-consumer handoff described in `RESIDENT-DEPLOYMENT-CANDIDATE.md`.

Reproduce with `sh scripts/test-resident-all.sh` and the four explicit environment selections documented in `RESIDENT-WORKERS.md`.
