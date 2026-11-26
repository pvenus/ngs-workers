# Workflow document worker: preset and portrait planning

Self-contained Node 24+ candidate. Run `node --test`. A fixed localhost
client, a read-only watcher, and a resident job poller are included.
There is no inbound server or installed launch configuration.
A trusted one-shot caller may invoke `runPresetFromPath({dbPath,
officialRequestId,taskId,pds})` only for one explicitly approved official
request and one exact task. No fallback or selection of other work.

The preset operation accepts `worker_type=document`, `task_type=npc.preset`,
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
`89c7568ce9e8723d01c50ef656c4259ea7babf94` (see vendored checksums).
Preset registry is a read-only projection of `preset('3')`, `LABELS`
and `template()` from NGS `6c632e5`,
`local/services/npc_lifecycle/presets.py`,
SHA256 `a8fcde2c02e164d785deef0fadca942126a91d70a1d71c6cbdd5fe73a6ea3575`.
The vendored projection SHA256 is
`d7ffc2aaee8f57132761a6a8a7e17048713dcf35c8fbce0de09dcf3231b03c5b`.
The profile matches the empty noncombat registered v3 slots; category IDs
are derived from the new stable preset task identity, not old result IDs.

Process health identity is `document-worker` (60-second OPS push while in resident mode).
Watch mode reports health after its scoped capability probe.


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


## Resident and idle planning

```sh
node cli.mjs --run --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite"
```

The resident reads the dedicated SQLite database directly via the shared
`workflow-ready.mjs`. It scans only its supported document task types, with a
five-second pause after a tick, and reuses the exact CAS before running one
task. Supported operations are preset, representative planning and idle
planning. An operation may be scoped by an explicit planning flag, and a
missing PDS capability leaves that type unclaimed. No remote queue API or
central assignment service is used.

Idle planning requires the same-run representative planning task complete,
not a representative portrait asset. It downloads and verifies both PDS
folders, selects current indexed character/expression concept and the exact
current representative planning text matching the PDS basis, and excludes
other NPC planning bodies. Only the single idle planning artifact is saved
through the opt-in idle PDS operation. The worker completes the selected row
only on `{"ok":true}`. A running task is never automatically rendered again.

Master Concept `concept.category-generate` works on one official category.
Add produces one full Markdown document from the approved title, outline and
requirements via the existing restricted renderer. Only historical persisted
replace workflows can still wait for the exact same-run file step. Migration sends no model request. It reads
the exact current scoped PDS-derived lossless Markdown and echoes it to the
PDS for a protected replacement; the worker reports
`conversion:"deterministic-markdown"`. There is no direct Filex access or
legacy NPC planning-folder input in this operation. Remove belongs solely to
the file worker.

Existing category edits use `operation: update`: exact PDS current Markdown and
SHA-256 are verified before the single restricted renderer invocation. Only the
full revised Markdown and expected source hash are submitted to existing PDS
category publish. No File task or deletion is used for updates.

On new add, one restricted call returns only `section_1`, `section_2`, and so on.
The worker constructs the one Markdown document using the registered H2 titles
and order. The body protocol does not ask the model to author or validate a
heading. A failed new add does not automatically render again.

## 공식 프랍 디자인 문서 단계

기존 Document worker의 `--run` → `prop.generate.design` / `prop.regenerate.design` (`prop_design`, 무선행) 단계입니다. PDS의 정확한 `workflow.prop-design-write.v1` 기능 응답을 확인하지 못하면 공식 요청을 잡지 않습니다. 선택한 요청과 같은 `project / propId / source pin / context SHA`만 허용하고 PDS에서 받은 Master Visual 필드 두 개와 Prop Design Markdown을 독립 해시 검증합니다. 풀 재생성의 이전 버전은 비교·출처 고정용이며, 오래된 이미지나 캡션을 기존 디자인 대신 모델에 넣지 않습니다.

고정 Codex CLI 권한 읽기 전용, 도구/웹/플러그인 비활성, 임시 경로·입출력·시간 상한을 재사용합니다. 요청/자료는 형식화된 입력값 하나이며 임의 경로·실행 코드가 아닙니다. 결과는 하나의 `ngs.prop-design-spec.v1` 메타데이터입니다. 제목, 한 프랍 기본 이미지용 설명, 명시적으로 요청한 경우의 한 동작, 픽셀·비픽셀 이미지 자체의 독립 클릭 범위를 포함합니다. 범위는 정수 천분율로 기록하며 배치나 퍼즐 전이를 쓰지 않습니다. 설정되지 않은 동작이나 클릭 범위는 생성 후 암묵 적용하지 않습니다.

`POST /v1/props/ownership/workflow-design`는 작업의 정확한 claim과 하나의 typed artifact를 전달합니다. PDS가 `{ok:true,resultRef:"prop.design-spec.<canonical JSON sha256>"}`를 확인한 뒤에만 작업의 결과를 기록합니다. ACK가 불명확하면 성공/실패나 처리 재실행으로 돌리지 않습니다. 이 단계는 독립 산출물 저장일 뿐 최종 프랍 Filex 확정이나 미디어 생성은 아닙니다.

### Packaged NPC planning guides

The document worker reads one version-controlled Markdown guide from
`guides/` for each built-in NPC planning category: overview, portrait art,
representative pose, idle pose, and combat pose. The operation must be in the
worker's own planning registry; no request can supply a filename or replace
the guide. Change its Markdown in this repository and release the document
worker to apply the revision. There is no NGS guide editor.

For every built-in category, the worker supplies the registered NPC intake
brief, the current Filex Master Concept character/expression sections, and
only the verified earlier documents from the same version that the category
needs. The guide provides authoring instructions; Filex content and upstream
documents provide project evidence. The existing section headings and JSON
output contract remain in force. Combat must name a concrete weapon in the
combat stance section; if canon has not specified it, label a compatible
choice as a proposal. Idle and combat exclude unrelated handheld props.

A preset write carrying the exact loopback PDS no-write rejection proof is a normal failure, eligible under the existing retry policy. A generic 409, disconnect, or uncertain receipt is not proof. Transient SQLite write contention only retries the same failure-state compare-and-swap, not the task or the PDS write.

A loopback POST with connection refused before establishing TCP did not send a
write and is a normal failure. If the connection was accepted and later breaks,
or the completion update is uncertain, retain reconciliation and record the
safe stage label. Do not replay any storage RPC to infer its outcome.

### Codex session lifetime
The resident worker owns a private stdio app-server connection. Reuse lasts at most
five minutes from process start for admitting new work. At expiry an active turn
can finish; an idle connection closes, and the next job starts a new process.
Each invocation uses one fresh ephemeral read-only thread and confirms its end
before unsubscribing; requests never resume or fork threads. An uncertain turn
is not replayed by the transport. The image worker still requires the pinned
tool invocation, arguments, output identity and PNG checks before storage.
Operational diagnostics record timings and fixed outcomes without prompts or
provider outputs. The transport modules are byte-identical in the image and
document scopes because OPS packages these targets independently.
