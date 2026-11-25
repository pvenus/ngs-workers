# Workflow document worker: exact NPC preset

Self-contained Node 24+ candidate. Run `node --test`. A fixed localhost
client and a read-only watcher are included. There is no inbound server,
job poller or installed launch configuration.
A trusted one-shot caller may invoke `runPresetFromPath({dbPath,
officialRequestId,taskId,pds})` only for one explicitly approved official
request and one exact task. No fallback or selection of other work.

The worker only accepts `worker_type=document`, `task_type=npc.preset`,
pending task and planned run. The exact official request ID, canonical
payload SHA and planned task identity/input are revalidated under the SQLite
writer lock. A replacement requires its **exact same-run completed delete**
as its only dependency-readiness rule. Changing dependency to NULL or using
the wrong predecessor never authorizes a replacement. A truly new NPC has
the NULL predecessor that Planner recorded.

The deterministic preset consumes only existing official character fields
and the pinned registered defaults; no model, old NPC data, earlier
incarnation, worker-side PDS ownership claim, admit or prepare-create is used.
The injected `pds.publishExactPreset` receives exact typed metadata, canonical
preset artifact plus SHA and an optional predecessor result reference as data.
A missing predecessor result reference does not block a claim. Only PDS
can verify and enforce target absence at the storage boundary.
The old `/api/npcs/create-character` route requires context-bound
`GUARD.operation='create'` and legacy Filex generation preparation. It is
**not** used, and its checks must not be bypassed. A future trusted PDS
adapter must provide an atomic scoped create (including any internally
assigned incarnation) and authenticated durable readback, not make an
external persistent reservation. The bundled outbound client requires the independently configured PDS
capability and verified receipt. This package does not provide PDS authority.

A matching committed/verified normalized result must bind exact official
request, task, project, NPC, official SHA and artifact SHA. Only the claimed
preset row is then moved to completed with its opaque receipt ID. Completion
checks the selected task/run binding, not parent readiness again. All other
task and run rows are unchanged. No RS status calls. Uncertain or mismatched publication leaves
the task unresolved without automatic repeat. A future authorized
read-only reconciliation operation is separate.

Planner contract is the exact pair of modules from
`f24d23a3fef72ed74ca6e0d93cb34287229530b5` (see vendored checksums).
Preset registry is a read-only projection of `preset('3')`, `LABELS`
and `template()` from NGS `6c632e5`,
`local/services/npc_lifecycle/presets.py`,
SHA256 `a8fcde2c02e164d785deef0fadca942126a91d70a1d71c6cbdd5fe73a6ea3575`.
The vendored projection SHA256 is
`d7ffc2aaee8f57132761a6a8a7e17048713dcf35c8fbce0de09dcf3231b03c5b`.
The profile matches the empty noncombat registered v3 slots; category IDs
are derived from the new stable preset task identity, not old result IDs.

Future real process health identity is `document-worker` (60-second OPS
push only after an actual successful capability probe). No change to legacy
document worker, owner settings or operational registration.


## Exact operator modes

```sh
node cli.mjs --watch --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite"
node cli.mjs --execute --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite" \
  --official-request-id '<exact official ID>' --task-id '<exact preset task ID>'
```

The watcher opens the SQLite connection read-only, checks the fixed
`GET http://127.0.0.1:8030/v1/npcs/ownership/workflow-preset` and sends
`{"serverName":"document-worker"}` to OPS 8090 only when the dedicated
capability returns the exact enabled schema. Checks start immediately and
repeat every 60 seconds. No claim or PDS write can occur in watch mode.

Execution first checks the same capability, then claims one exact selected
preset if ready. It sends one bounded POST, exact body
`{claim:{officialRequestId,taskId,projectId,npcId,payloadSha256,claimStamp},
artifact,predecessorReceiptRef}`. The claim stamp is the exact persisted
running-row value, artifact is a JSON object and predecessor receipt may
be null. Storage source revisions and authority are resolved by PDS, never
supplied by command arguments. Errors are not retried by the worker.

## Representative planning (explicit opt-in per invocation)

Use the same worker with the additional option below. Default invocations
still select only the preset operation.

```sh
node cli.mjs --execute --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite" \
  --official-request-id '<exact official ID>' --task-id '<representative planning task ID>' \
  --operation npc.plan-representative [--audit-dir '/absolute/private-new-directory']
```

The worker requires the exact PDS representative capability and a completed
same-run preset at claim time. The already-claimed row and its claim stamp
authorize the typed POST to the representative reader. Only the exact official
character and current, selected master concept and fresh preset context may
reach the renderer. PDS supplies an exact read basis; the original basis is
sent back with the content-only artifact. No previous-incarnation or backup
content, ownership claim, preset fallback, or worker-side source CAS is used.
The ordinary PDS write acknowledgement is required before this worker completes its
exact row. Unknown outcomes do not cause a second render or POST.

Optional audit is permitted only for this explicit execution; input, exact
prompt, and result go to separate private files, not worker stdout. To check
representative PDS capability without claiming or rendering, append
`--operation npc.plan-representative` to the watcher command. Its healthy
heartbeat reflects the selected PDS capability, not a live inference test.

### Representative folder-snapshot source mode

Use the same explicit representative CLI flags. Its readiness probe is now
`GET /v1/npcs/ownership/workflow-plan-representative?inputMode=folder-snapshot`.
The scoped task read sends `{claim,inputMode:"folder-snapshot"}` to the existing
`/read` handler and accepts only exact claim/CAS basis, character and current
preset metadata. The old master concept projection is never fetched or rendered.
The publication body is unchanged. PDS advertises
`workflow.representative-plan-write.v1` and returns exact `{"ok":true}` only on
successful storage. The workflow row alone decides duplicate execution and
completes without a receipt ID; failures or unknown outcomes still require
reconciliation. Preset publication retains its existing receipt contract.

The existing PDS MCP tools enumerate the complete recursive inventories of
`binari/concept` and `binari/npcs/<npcId>/planning` using empty local manifests.
Each file is fetched by exact path, size and SHA, in chunks, into the two private
temporary input directories. Both manifests are checked again before rendering;
any changed inventory aborts. Every file is read and validated again, linked
to its exact scope, relative path, size and original SHA. For representative
planning only, the worker parses the exact `sections/character` and
`sections/expression` metadata and every field referenced by their current
`fieldIds` indexes. It verifies path/ID correspondence and the shape of each
selected file, then supplies section titles, descriptions and summaries plus
indexed field labels, values and summaries. New indexed fields in either
section are included without adding a worker allowlist. A missing or malformed
indexed field fails closed. Other concept sections, unindexed files, and all
NPC planning contents are excluded from parsing for the prompt. Official
request intent and fresh preset metadata remain separate and unchanged.

The optional local input audit traces all verified files with path, size, digest
and disposition without storing skipped content. There is no model
summarization, implicit fallback or silent truncation. Prompts over 192 KiB fail
closed. Temporary downloads are removed on success or failure; opt-in audit
files remain private and separate from download scratch.

### Independent representative image worker

This worker is a separate process, not a central executor. Configure the
existing trusted image host with `WORKFLOW_IMAGE_HOST_CONFIG` (absolute path to
an owner-only JSON file) and `WORKFLOW_IMAGE_HOST_CONFIG_SHA256` (its exact SHA).
The host config must pin the installed Codex binary, legacy Workers and NGS
modules, and already-approved output directories. The renderer does not accept
commands or paths from the task. To check the configured pins without starting
a model, run from the release directory:

```
node --input-type=module -e 'import {probePortraitRenderer} from "./portrait-renderer.mjs"; if (!await probePortraitRenderer()) process.exitCode=1'
```

Modes (`--db` is the canonical absolute existing planner DB path):

```
node image-cli.mjs --run --db /absolute/workflow.sqlite
node image-cli.mjs --watch --db /absolute/workflow.sqlite
node image-cli.mjs --execute --db /absolute/workflow.sqlite --official-request-id ID --task-id ID
```

Only `--run` polls and automatically processes runnable work. It picks at most
one pending representative image at a time from planned runs after the exact
same-run representative document is completed. The read-only lookup shared in
`workflow-ready.mjs` cannot claim work; the normal exact SQLite CAS authorizes
the claim. `--watch` checks readiness without selecting work. `--execute` is an
explicit one-shot through the same authority path. The OPS heartbeat timer
reports the ready image service separately from the asynchronous renderer;
phase logs contain no source or image content.

The worker checks PDS's current claim-bound basis, downloads and verifies both
complete source folders in private scratch, and uses only the indexed master
character/expression sections and exact representative category whose content
agrees with PDS. The legacy native image runner must return one actual-tool,
verified PNG. The worker sends that original to the exact PDS portrait route
and completes only its own claim after the exact ordinary write ACK. Unknown
outcomes remain running for reconciliation, never a fresh claim or second
publish. Provider-managed original output follows the legacy provider's file
ownership; worker-owned temporary files are cleaned up. No permission changes,
live model startup or deployment are performed by installing the files alone.

The image resident also handles the exact `npc.portrait-idle-reference` after
the same-run `planning_idle` completes, then the eight exact directional
`npc.portrait-direction` tasks after that idle reference completes. The same
CLI needs no change for resident execution; one-shot testing may append
`--operation npc.portrait-idle-reference` or
`--operation npc.portrait-direction` after the exact IDs. Each stage requires its
own PDS readiness before claiming. Idle reference uses the current representative
PNG; directional portraits use the current idle reference PNG with legacy
camera-view vocabulary, never a flat image rotation. The worker checks the
reference bytes against their canonical asset name and SHA before attaching one
private local reference, which is cleaned with render scratch. Same exact PDS
`{categoryId}` and ordinary write ACK, no version or receipt contract. The image
worker does not claim or run the resulting animation or frame tasks.

In resident `--run` mode, SIGTERM/SIGINT stops accepting new tasks and drains
the current task without aborting its renderer, publication or health pulse.
One-shot cancellation is unchanged. This does not protect against external
SIGKILL or a service supervisor's forced-exit deadline; use confirmed idle or
signal and wait for normal exit before replacing the resident.

Once an image claim is acquired, a failure before the publication request is
sent records `failed` on that exact running claim. It does not unlock any
dependent task immediately. The resident can claim that exact failed task once
its 10-minute cooldown elapses, at most three retries (in addition to the
initial attempt). Claiming a retry increments its counter atomically; claiming
pending work does not. If publication was attempted but its
result or the completion update is unconfirmed, the task stays running for
reconciliation rather than risking another write.

Fresh four-document workflows consume only the exact current role-tagged
portrait-art category's `이미지 제작 지시` and selected pose category's
`포즈 제작 지시` Markdown blocks, with the official character identity. The
worker still verifies both complete folder snapshots, but does not send raw
Master Concept or unrelated planning text for fresh jobs. If a fresh
workflow's approved blocks are missing or malformed, the worker fails closed;
old staged Markdown without fresh markers retains its previous input shape.

Idle references additionally wait for the same official run's exact completed
representative image, even when the independent idle plan is already complete.
Representative output must pass the pinned native decoded PNG transparency
contract before canonical write. This checks PNG and alpha, not the subject
matter or whether it visually matches the character.

### Neutral 2D motion reference

The existing image worker handles the exact `motion-preset.convert-2d.image`
task (`motion_2d_image`) in the formal motion preset conversion workflow.
It checks the original request and selected task against the workflow DB,
then verifies the source preset through the claim-bound PDS route. The native
image adapter accepts only the fixed neutral-reference marker, with no NPC
identity, visual source attachment, or request-supplied path. It asks for one
flat monochrome, faceless full-body mannequin on a transparent background.

The PNG is published through PDS with its content hash. The worker marks only
its exact task complete after the matching receipt; errors after dispatching
publication remain reconcilable. No NPC portrait, project catalog item, or
other workspace is created by the image worker.

Cinematic source images are a separate official `cinematic.source.generate` job. The existing resident image worker validates the exact planner/PDS text, then reads the current canonical Binari Master Concept from loopback PDS. It selects only the active **시네마틱 영상 디자인** category and provides its digest-verified Markdown as the shared visual style, separate from the exact shot title and prompt. If the document is missing, archived, or invalid, the job does not call the model. The worker logs the selected design digest on success, uses the pinned native scene provider once (`transparent_background=false`), and publishes the original 1536×1024 PNG to the source library. It does not enqueue animation or interleave with other image calls. Unsupported geometry/format is rejected rather than resized; ambiguous native generation is not automatically repeated.
