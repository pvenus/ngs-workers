# Workflow file worker: exact NPC deletion candidate

Node 24+ built-ins. Self-contained, with exact copies of Planner
`planner.mjs` and `envelope.mjs` from
`f24d23a3fef72ed74ca6e0d93cb34287229530b5` under `vendor/planner-f24d23a/`.
The worker imports only revalidation/JSON helpers; the vendored database store
is used by the test fixture only. Run isolated tests with
`node --test`. Nothing runs on import or install. The candidate exports
`runDeleteFromPath({dbPath,officialRequestId,taskId,pds})` for a trusted
explicit one-shot caller. An existing canonical dedicated workflow
database must have owner-only permissions; no schema or new fields are created.

The worker accepts one explicit official request and task ID, never the next available task. It
rebuilds the deterministic exact operation and NPC from the stored official
payload, compares the canonical payload and RS SHA, and verifies the selected
task, worker type, planned run and dependency inside one SQLite writer
transaction. The current Planner makes deletion the first task, so its exact
frozen predecessor is NULL; a database-only dependency edit does not authorize
execution, even if it points to a completed task.

A one-row CAS changes only the selected task from pending to running before
contacting storage. The injected `pds.deleteExactNpcFolder(command,claimStamp)` is called
at most once; no RS or legacy Filex lease is queried by this worker. The
command is exact typed metadata: official request ID, task ID, project, NPC,
payload hash, exact-folder scope, history preserved, no old data reused. It
never accepts a filesystem path. An adapter must establish trusted PDS
execution authority and supply exact generation/source CAS using the separately reviewed server contract. The local port sends the exact
claim stamp, letting PDS independently verify the running task. A workflow DB row is not a PDS credential or proof
of safe concurrency.

The adapter must verify a committed deletion and exact target absence, then
normalize to `workflow.pds-delete-receipt.v1` with the original
`officialRequestId,taskId,projectId,npcId,payloadSha256,operation`,
`status:"committed",verified:true,targetAbsent:true,receiptId`. This is an
internal interface, not a PDS wire contract. A valid receipt lets the worker
directly move only its own claimed task to completed and store the opaque
receipt ID in result_ref, through another exact one-row CAS. All other tasks
and the entire workflow_runs table remain byte-for-byte unchanged; downstream
readiness derives from the completed predecessor later, not another write.

A lost response, wrong receipt, or failed final CAS is reported as requiring
reconciliation. The worker never resets/reclaims that row, even after
restart or an early crash, and never calls delete again for a running or
completed task. A future reviewed read-only receipt reconciliation flow is
needed for such cases. The runner/localhost transport are included, but leave live launch and
deletion authorization entirely to an approved operator. The PDS capability
and independent authority must be in place before enabling this worker.

The vendored source is pinned by `vendor/planner-f24d23a/SHA256SUMS`. For
provenance, compare the exact blobs against the same paths in the upstream
Planner commit named above. Deployment need not mount or import another repo.


## Explicit operator commands

Watcher, no task selection or deletion:

```sh
node cli.mjs --watch --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite"
```

This process checks the database read-only and requires exact HTTP 200
`{enabled:true,schemaVersion:"workflow.pds-delete-receipt.v1"}` from
`GET http://127.0.0.1:8030/v1/npcs/ownership/workflow-delete` before sending
`POST {"serverName":"file-worker"}` to the fixed OPS heartbeat endpoint
`http://127.0.0.1:8090/api/service-heartbeat`. Capability failure suppresses
healthy reports; OPS delivery failure is logged separately. It checks
immediately and every 60 seconds, and has no listener, task list or POST to
PDS. There are no runtime URL or route overrides.

Only after action-time authorization for the single exact NPC folder:

```sh
node cli.mjs --execute --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite" \
  --official-request-id '<exact official ID>' --task-id '<exact deletion task ID>'
```

Requires the same ready capability before any claim. Only one bounded
POST can follow a successful exact local claim, with body `{claim:{...}}`.
The PDS authority must back up and independently verify the deletion and
absence before returning its normalized receipt; only then can this worker
write completion. A lost response is never retried by the worker.

## Directional native PNG postprocessing worker

Launch only after PDS reports the exact `workflow.frames-write.v1` capability:

```sh
FRAME_ALPHA="$HOME/Workspaces/Workers/runtime/frame-alpha"
node postprocess-cli.mjs --run \
  --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite" \
  --config "$FRAME_ALPHA/adapter-config.json" \
  --model-pins "$FRAME_ALPHA/verified-model-hashes.json" \
  --worker-source '<absolute reviewed Worker release with frame_full_alpha_cli.py and frame_processing.py>' \
  --sam-source "$FRAME_ALPHA/sam2-source"
```

This process polls the shared ready lookup and sequentially claims only
`npc.frames-postprocess` rows with their exact motion+direction animation
predecessor completed. It reads only the current canonical PDS native manifest
and verifies every PNG against the exact predecessor, byte count, order and
SHA-256 before calling the installed offline BiRefNet/SAM2 matte pipeline.
It does not download models, read Filex directly, or encode/extract video.

The existing BOX half-size + 32-color + binary-alpha implementation runs once
for each direction. Crop and palette are common across frames of that direction,
not across all eight directions. The original native sequence remains untouched.
Each attempt uses private temporary files and removes them in normal success/failure.
Only the new alpha and pixel PNGs and manifest pair are sent to PDS; its success
ACK permits CAS completion for this exact task. A read or render failure closes
the exact task as failed; an uncertain publication leaves it running and never
retries automatically. The OPS heartbeat is independent every 60 seconds while
PDS advertises availability. No operational app changes on install/import.

Isolated checks: `node --test`, and `postprocess-render.test.py` with a reviewed
Worker source on PYTHONPATH and Pillow installed. The latter generates its own
synthetic PNGs and stubs only learned alpha inference.
