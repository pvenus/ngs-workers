# Candidate service composition and transition gates

No production binding or LaunchAgent is changed. OPS remains outside worker
logic. Each worker can run in its own process using the resident entrypoint:

```
python3.12 -B -m workers.resident document --check --config /operator/deployment.json
python3.12 -B -m workers.resident document --config /operator/deployment.json
```

The second command is not an instruction to activate the candidate release.
`--check` validates configuration and the factory SHA-256 without importing the
factory, opening a queue, contacting services or loading a model. No-config start
fails closed. Config requires schemaVersion `resident-deployment.v1`, canonical
role/capabilities, workerId, absolute resolved factoryPath, approved factorySha256,
activationApproved=true, and operator-owned settings. The factory's build_ports
returns `Ports(requests,pds,backend,residency)`. It is executable operator code,
not a request payload or an untrusted user-supplied module. Pin the entire deployed
package separately; a factory hash alone does not pin transitive dependencies.
Workers use HttpRequests; only the Request service instantiates DB authority and
FormalPayloadSelector. Model roles require a residency authority. Stop signals
cancel the active operation, and unconfirmed execution never automatically frees
resource ownership. `--once` is available for isolated service validation.

## Formal selection

FormalPayloadSelector takes trusted exact `(request_type,payload_schema,domain)`
resolver/validator registrations, never a prefix guess. The resolver reads the
original persisted domain payload, not UI summaries. The selector checks its full
canonical hash against the authoritative payloadRef before returning it intact.
Ready gating is mandatory and belongs to the Request/workflow authority; the
selector skips blocked dependencies instead of blocking later runnable requests.
CommonAuthorityBridge repeats scope/hash checks and claims/reserves atomically.

Music uses MusicIntake.payload and its original binding. Portrait selection must
return the full original portrait record and binding from characterRef/context,
not merely the image-generation prompt. Existing domain-specific validators
remain required registrations. Missing validators/resolvers are unsupported,
not a fallback into a generic model request.

## NPC document compatibility gate

There are TWO historical envelopes, neither equals a category request DTO:

* standalone: npc.category.request / npc-category-request-transfer.v1 /
  legacy.pipeline; hash covers transfer snapshot.
* aggregate candidate: npc.workflow / ngs.npc-category-candidate.v1 /
  npc.category-candidate; hash covers parent/stage/request/actor/variation binding.

The latter is draft production. Its parent owns selection/CAS apply. It must not
be relabeled canonical publication or completed by a new worker just because CLI
returned. DocumentDraftWorkspace therefore publishes only through a trusted
parent candidate port, reads back exact packet/attempt/fence proof, and declares
sourceApplied=false. It never invokes file replacement. Its receipt must be
verified server-side as a candidate receipt, and the existing parent must consume
it before any canonical apply. These routes remain unregistered until that real
parent port and original-envelope compiler are integrated. No new request type
was invented to bypass this boundary.

## Release acceptance

Before activation, finish exact domain resolvers/validators, authenticated PDS
bindings and the legacy document parent bridge; pin complete code and native
binaries. Drain existing work without modifying active frame cleanup. Switch
ownership exactly once, disable old consumers for migrated routes, preserve
existing IDs/attempt keys/history. Isolated fixture tests prove mechanics, not
production model success, GPU coexistence, or original-data publication. Review
all test skips and run dependency-equipped suites before declaring completion.

## Subsequent candidate wiring

`deployment.role_factory.build_role_ports` now composes real HttpRequests,
authenticated ownership-guard retrieval, per-claim PDS clients, document draft
HTTP, model-residency HTTP, and the reviewed role backends. Full/half frame dispatch
separates CPU-only half work from model-residency-requiring full work. No factory
is installed into a live process. Model binaries/config, native reviewed runner,
media source reader and cleanup still come from the pinned operator factory.

`DomainResolvers` reads actual animation/frame body tables, MusicIntake's original
DTO, portrait persisted envelope, and ParentDraftBridge's category envelope.
Every enabled route requires its domain validator; PDS snapshot bindings are
separate from the immutable request. `validate_document_envelope` is the explicit
single-candidate category compiler boundary. The standalone legacy transfer
route remains unsupported. Request/PDS services must install authenticated route
handlers and review the chosen enabled routes before activation.

`ParentDraftBridge` now connects the original parent executor's `_category`
factory to the existing CategoryService.prepare method with a saved-output-only
generator. Existing PDS source/incarnation/revision/native-field validation runs;
the saved candidate moves to awaiting_selection. The original parent's receipt
filename and fields are preserved. Parent apply is not called and ownership is
not released. Draft HTTP authenticates the worker and the issued claim. The
Session resolver checks child membership and an existing parent journal before
constructing a Session, and never calls pin/admit. This supersedes the earlier
statement that the candidate parent bridge was not implemented, but does not
activate or migrate an existing request.

`deployment.document_parent.build_parent_bridge` supplies the concrete trusted
legacy binding. Pass the reviewed CategorySingleExecutor, CategoryDispatch,
original parent service lookup, original Session class, and npc_ownership module.
The dispatch uses the existing nested local request and updates its pipeline
awaiting_selection/evidence state, not just the category proposal DB. Its existing
PDS reads run under the already-admitted guard/stage context; no enter/pin call
creates new authority. The Request Common lease remains the completion owner.
`deployment.request_service.build_handler` mounts claims/ownership guards,
parent-draft API and model residency under one authenticated Request service.
No HTTP handler is started merely by composing it.

Background service lifecycle now has RequestBackgroundLoops for the automatic
reconciliation supervisor and NPC single-result parent continuation. Start/stop
belongs to the Request service; OPS only supervises the service process. The
latest worker contract checkpoints the PDS receipt durably in Request authority
before scratch cleanup, so an interrupted completion acknowledgment cannot erase
the only recovery proof. ParentDraftBridge exposes service-only immutable receipt
inspection for reconciliation; it does not reuse expired worker authorization.
