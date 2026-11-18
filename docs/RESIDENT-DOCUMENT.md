# Resident document backend (candidate, not activated)

`workers.resident.backends.document.DocumentBackend` migrates the execution
boundary from NGS `concept_proposals/independent_session.py`: one ephemeral
Codex CLI invocation, read-only sandbox, no MCP/web/shell/image tools, structured
JSON output and explicit rejection of tool events. `load()` validates the
configured binary exists; it does not call Codex. Existing login is used.

Service-owned configuration must supply an absolute approved CLI path,
`output_schema`, and a trusted `validate_output(value)` function that raises on
invalid domain output (or returns False). CLI output is parsed and validated
before the backend returns an artifact. No executable path, shell arguments,
validator or schema is accepted from the user request.

`Claim.parameters` remains the original official request, not a replacement
request. An existing `intent` field is required. Different domain request shapes
need an explicitly reviewed adapter; this implementation does not guess mappings.
PDS may stage bounded JSON context as `document-context.json`; that file is data,
not permission to execute tools. No request payload hashes or reference bindings
are replaced here. The formal Request/PDS adapters must verify them.

Only the PDS-staged workspace is used. A start marker prevents a second CLI call
for an ambiguous/repeated attempt. Existing reserved output names and symlinks
are refused. Cancel and timeout terminate the process group and confirm exit;
unconfirmed exit raises `DOCUMENT_PROCESS_EXIT_UNCONFIRMED` and must retain the
resource/workspace for reconciliation. Neither retry nor model-call recovery is
implemented here. The service controls attempt identity and retry policy.

Returned `response.json` is temporary, not a published original. PDS owns CAS
publication and cleanup. This backend never writes Filex, changes an NPC, marks a
request completed, or schedules a worker. Production domain mapping, approved
binary pin validation, deployment and real Codex generation are not performed.

Tests use an isolated fixture CLI only:

```
python3.12 -B -m unittest discover -s tests/resident -p 'test_document*.py' -v
```

## NPC planning mapping and pin validation

`NPCDocumentBackend(binary, sha256)` supports the existing
`npc-category-request.v1` single-result add/modify payload without rewriting it.
Overview, common art and pose planning all use this category contract. Other
schemas fail closed, rather than being inferred. A PDS-staged context is mandatory
and binds request ID, attempt ID, canonical original-request hash, input refs and
expected revision. Binary SHA-256 is checked at load and immediately before each
execution. Deployment must supply the approved hash, never compute approval from
an untrusted request. No operating binary or launch configuration was modified.

`NPCContextStager` migrates the existing catalog/read context contract. Its call
port is supplied by the PDS transport, not an NGS import or filesystem reader.
It checks project, NPC, revision, source version, selected category IDs, catalog
hash and actual content hash. Optional `CategoryFileClient` downloads the target
file through the existing PDS file endpoint. Add uses the existing deterministic
UUID5(category request hash) identity. The CLI authors complete `fileContent`;
validation rejects any unrelated field change. `field:` edits preserve all
other metadata fields. No Python rewrite silently repairs the model's result.

This is context staging and generation, not a complete PDS lifecycle adapter.
The caller still owns staging registration, current incarnation/ownership guard,
CAS replacement, verified publication receipt, and cleanup. The output remains a
proposal until that lifecycle completes. Pose-specific category title selection
and supporting category ID selection must come from the trusted workflow adapter,
not user-supplied executable configuration. Real service/model E2E is deferred to
operational acceptance, with no live test requests created during development.

`DocumentDraftWorkspace` now supplies the temporary workspace lifecycle for a
parent-owned candidate: parent authorization → exclusive attempt directory →
context staging → CLI result → durable draft intent → submit/readback → cleanup.
The draft port is a required trusted bridge, not an invented live endpoint.
Existing-attempt detection requires reconciliation. Claim mismatch refuses
cleanup. Canonical file replacement remains with the historical parent. These
legacy routes are intentionally not enabled in CommonAuthorityBridge yet; see
RESIDENT-COMPOSITION.md for their exact envelope and transition gate.

Parent bridge implementation is now present in
`adapters/document_parent_bridge.py` with authenticated draft HTTP and existing
Session resolution. It uses the original CategoryService.prepare validation and
writes only the existing parent-owned candidate receipt, never canonical apply.
The envelope remains the original hash-bearing object in Claim.parameters; the
trusted NPC compiler reads its request member without replacing the envelope.
The new route is opt-in candidate wiring, not an operational migration. Legacy
standalone transfer requests remain excluded.

## Automatic single-result application (no new user approval)

`awaiting_selection` is an internal candidate checkpoint, not the intended user
waiting state. `ParentAutoApply` watches migrated resident child completion and
advances the accepted one-result parent automatically. It permits only
worker-count.v1 + one invocation + one category result. Its completed-only
executor returns the existing durable draft and cannot prepare or execute a new
model. The existing parent's finalize_result invokes the registered
finalize_one_result/confirm path with the original hashes, then PDS CAS and
verified publication. Completion is reported only with the parent's durable
sourceApplied=true/verified publication proof. Comparisons, legacy transfer
requests and unrelated historical results are not auto-selected.

RequestBackgroundLoops runs this continuation alongside reconciliation inside
the Request service. Factories supply a fresh original parent service object for
each call. Service startup must ensure the old consumer cannot concurrently own
migrated parents. Unknown or blocked operations retain their original error and
recovery rules; the loop never clears a lease or sets queued by guessing.

Master concept is separate: existing v3 concept requests use
request-authorized-single-auto-apply and their own drain_one/PDS file pipeline.
The NPC parent hook does not handle master concepts or claim to migrate that
service. No extra user confirmation is introduced for either approved single-
result path.
