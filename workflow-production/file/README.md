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

The exact runner accepts one explicit official request and task ID. The resident
uses the shared read-only candidate lookup to select one ID pair at a time. The exact runner
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


## Resident File Worker

```sh
node cli.mjs --resident --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite"
```

The database path is explicit, private and pre-existing. The File Worker uses the
same byte-for-byte `workflow-ready.mjs` as the Document and Image Workers. It only
selects pending, planned, result-less `file / npc.delete-folder / delete` tasks
with no predecessor, oldest first. Selection is read-only; the unchanged exact
runner revalidates and claims the row transactionally before PDS. The worker
does not poll RS, run a generic command, or accept injected paths or task types.
The existing independent PDS authority and per-task authorization are required;
the queue selection does not itself confer deletion authority.

It checks for one task every second and awaits the entire exact attempt before
selecting another. The existing OPS heartbeat checks immediately and every
60 seconds, independently of deletion duration. SIGINT/SIGTERM stop admission
and drain a dispatched attempt; an uncertain result is left running for
reconciliation and is never re-issued. The read-only watcher and exact one-shot
remain explicit separate modes. No deployment or automatic start is performed.

The resident also claims exact `concept.category-delete` tasks for formal
Master Concept replacement preparation and removal. The planner does not
schedule file work for add or migration. The worker sends only a scoped
claim to the paired PDS; it does not access Filex or perform a local delete.
The concept route's exact successful ACK completes only that file task.
Unrecognized or ambiguous outcomes remain running for reconciliation;
recognized definitive prewrite rejection can enter existing cooled retry.
The NPC delete worker behavior and verified receipt requirement are unchanged.
For a trusted explicit one-shot use `--operation concept.category-delete`
after the existing exact DB, request and task arguments.

## Shared delete execution contract

After verifying the exact stored task against the official Planner DAG, the
shared file worker projects it to one machine-only execution command:

```json
{"operation":"file.delete","targets":[{"kind":"concept-category","id":"category.add.0123456789abcdef0123456789abcdef"}]}
```

The other supported kind is `npc-directory`. This DTO carries neither display
text nor filesystem paths. It is paired with a separate verified claim binding
(official request ID, workflow task ID, project ID, payload SHA-256 and claim
stamp). Each independently planned workflow task claims exactly one target;
a multi-target command is rejected, not split or partially executed.

Compatibility: persisted task types/keys, task IDs, predecessor relationships,
and input shapes stay unchanged (`concept.category-delete/category_delete`
and `npc.delete-folder/delete`). There is no schema migration or resubmission.
`file.delete` is an execution operation, not a new database task type.
The loopback adapter selects the preexisting PDS API by target kind, forwards
the original exact claim wire, and preserves the existing result verification,
scope rules, and storage-owned backup/fencing behavior. The file worker does
not interpret category titles, prompts or physical paths as deletion targets.

The resident worker also applies `cinematic.playback.update` via the PDS timeline revision check and `cinematic.sequences.remove` / `prop.animation.remove` against the exact selected IDs. These change playback/index metadata only and do not claim an image or animation model. They use dedicated PDS mutation endpoints, not the filesystem delete endpoint.

Published map deletion is one scoped `file.delete` / `file_delete` task per
map. It requires PDS map readiness (`/v1/storage/delete-target/map`) and sends
the exact claim and one map-directory target through the common storage delete
port. PDS and Filex enforce the frozen latest map revision and publisher;
this worker does not directly access map files or source backgrounds.

A published assembly uses the same single-target `file.delete` request. The worker
checks the assembly deletion capability (`/v1/storage/delete-target/assembly`) and
claims one exact directory under `canonical-files/binari/cinematics/assemblies`,
with the expected revision and publishing request verified against the official
plan. It does not delete the referenced map, prop assets, or unrelated folders.
