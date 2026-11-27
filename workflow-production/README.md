# NGS workflow worker release sources

This directory pins the verified running workflow sources separately for six
distinct production worker services. See `components.json` for the known
previous release IDs and entry points. Planner, RS, PDS and OPS are managed
in the services repository. These worker sources consume existing workflow
tasks and storage contracts; copying the directory does not start a process.

The current audio snapshot and the independently selectable resident audio
variant both use the existing ACE-Step HQ composer plus renderer and the same
published audio contract. The resident variant keeps the provider loaded for
five idle minutes across official requests; it needs its matching native-audio
source, existing models and interpreter, and a separately managed local binding.
It does not silently switch to another model. No native models or private
bindings are in Git.

When preparing a release, archive this directory from the verified Git commit
into a new immutable release directory. Run the worker's model-free tests in
its component directory (`node --test`). Bind each process to the existing
local service configuration. Runtime files, generated assets, databases,
keys, logs, and installed third-party/models stay outside this checkout.
Retarget managed paths only after verifying that the corresponding runtime
pins still match and that the worker has no active task. Never replace the
current service first and assume its old paths will still be valid.

### Completion writes on a busy workflow DB

After a publication receipt is validated, each worker retries only its exact
SQLite completion transaction on `SQLITE_BUSY` (including extended BUSY codes),
at five-second intervals, up to ten retries. It does not rerun a model, resend
a publish/delete request, retry a claim mismatch, or treat `SQLITE_LOCKED` as
BUSY. On exhaustion, the DB row is left unchanged and the worker logs
`workflow_sqlite_busy_exhausted` with the task ID; OPS alerting is separate.
The runtime copies of `shared/sqlite-busy-retry.mjs` keep the existing immutable
per-worker packaging scopes. Update each byte-identical copy when editing the
shared source; the test suite verifies parity.
