# Resident image and music backend adapters

This is a development candidate, not an operating service migration. No active
request routing, model process, launch configuration, or source data is changed.

## Official boundary

The resident runtime receives an authenticated `Claim` from its RequestsPort,
stages inputs through PDS, invokes a backend, and publishes only through PDS.
The backend never reads Filex directly, claims its own work, completes a request,
or publishes assets. `Claim.parameters` remains the original official request
DTO. Trusted deployment code selects the backend factory. Neither parameters nor
a staging manifest may choose a Python module, class, shell command, or runtime.

PDS must create `execution.json` in its attempt workspace:

```json
{
  "schemaVersion": "workers.execution.v1",
  "requestId": "official request id",
  "attemptId": "official attempt id",
  "capability": "image.generate",
  "expectedRevision": "source revision",
  "requestHash": "canonical SHA256 of original Claim.parameters",
  "inputRefs": [],
  "inputFiles": [],
  "execution": {}
}
```

Each inputFiles item is `{ref,path,sha256}`; it must correspond exactly and in
order to inputRefs. Paths must be relative, regular files within the staged
workspace, with no symlink ancestors. All bytes are hashed before execution.
The workspace and artifacts directory are attempt-exclusive. An existing
artifacts directory is rejected instead of silently repeating generation.
This envelope is an internal PDS adapter contract, not a replacement user DTO.
It is not currently a production PDS endpoint implementation.

## ImageGenerationBackend

`workers.resident.backends.image_generation.ImageGenerationBackend` accepts a
trusted `provider_factory(workspace)` whose port implements
`run(native_claim, workspace, cancel) -> (png_bytes, proof)`.

`execution.providerClaim` contains the existing pinned native descriptor:
index, providerClaim, requestHash, providerRequest, providerRequestHash.
The provider must be `codex.image_gen` / `image_gen.imagegen`. Only prompt,
transparent_background and verified staged referenced_image_paths are accepted.
Editing requires references. Returned proof must match every pinned identity,
actualToolOutput must be true, and SHA256 must match the returned PNG bytes.
PNG structure, CRC and bounded decoded size are validated before writing.
NPC-specific pose/alpha policies remain above this generic capability.

**The legacy native image task runner is not yet bound to a daemon-safe port.**
Its existing client must gain a cancellation/outcome reconciliation contract
before it can be installed here. An absent provider fails with
IMAGE_NATIVE_PROVIDER_NOT_BOUND. There is no fabricated image, alternate model,
or synthetic-production fallback. Synthetic ports exist only in isolated tests.

Image is an API capability, not a resident local model. Its cheap load validates
the trusted factory without importing a model or invoking a provider.

## MusicGenerationBackend

`workers.resident.backends.music_generation.MusicGenerationBackend` uses the
existing AudioForge HQ request validator and HQBackend. The optional
`hq_engine_factory` is built only by trusted deployment code with explicit runtime,
runner, interpreter, inherited two-lock descriptors, validator and enable flag.
HQBackend retains its runtime and weights pins, operator controls, inherited
resource checks, ordered LM4B then XL-SFT stages, and subprocess cancellation.
The adapter does not download models, change pins or choose a legacy fallback.

Original prompt/duration/seed/model/generation_settings pass unchanged. Existing
HQ model and duration constraints remain enforced. Reference-audio inputs are
not supported and are rejected explicitly. Output is SHA-verified 48kHz stereo
16-bit PCM WAV with the requested duration. Receipt metadata is allowlisted;
prompts, local paths, and generated LM codes are not exported.

**The service is resident; the HQ model is not resident in this adapter.**
Existing HQBackend starts and joins per-job processes, with LM exiting before
XL-SFT. `load` does not claim that weights stay loaded. True model residency and
production trusted binding remain separate integration work.

## Cancellation and uncertain outcomes

Factories/ports must return only after local children stop. Cancellation before
execution causes no provider call. A provider normal return followed by lease
loss yields no published result. `ExecutionStopUnconfirmed` propagates without
cleanup or resource release. Other exceptions after a provider starts are treated
conservatively the same way, including when cancellation is set: a timeout alone
cannot prove a remote operation or child has stopped. The runtime records
execution_unknown, stops polling, and preserves the attempt for official
reconciliation. No new attempt, fallback, resource unlock or direct worker call
is performed by these adapters. Provider-specific confirmed-safe failure typing
can be added later to avoid unnecessary manual reconciliation.

## Isolated validation

```
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.12 -m unittest tests.test_image_music_backends -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=modules/audioforge /opt/homebrew/bin/python3.12 -m unittest discover -s modules/audioforge/tests -p test_hq_backend.py -v
```

Tests use temporary files and synthetic provider/subprocess fixtures, not actual
model inference. They cover original DTO preservation, native proof, source hash
and attempt binding, symlink/output replay refusal, cancellation, missing factory,
HQ no-downgrade, PNG/WAV validation, and real ResidentWorker publish/cleanup and
uncertain-execution resource-retention branches. Production authentication,
provider availability, PDS staging translation, and actual inference remain
unverified until separately authorized integration tests are performed.

## Daemon/PDS connection candidate (follow-up)

`image_native.NativeImageCLI` now provides actual subprocess stdin/stdout handling,
cancellation and process-group joining. `NativeImageProvider` composes this client
with the existing reviewed native runner via trusted `pinned_native_runner`.
Source-specific argument checks, started markers, actual nested-tool argument
verification, and tool-output proof remain in that runner. It does not invoke
models in tests. Approved native tool output roots are configured read roots,
not user-selected destinations. Remote outcome after interruption remains
uncertain even if the local CLI was successfully stopped.

`deployment.image_music` now exposes image/music factories and PDS workspace
composition. `adapters.image_pds` and `music_pds` implement attempt staging, exact
input byte checks, target pin persistence, media artifact publication, receipt
and asset byte readback, and ownership-aware cleanup callback delegation.
The media PDS backend is developed separately: `/v1/media-artifacts/publish`,
`/v1/media-artifacts/receipts/{requestId}/{attemptId}`, and asset readback.
These are immutable **draft artifacts**, not canonical NPC slot selection or
music-selection mutation. PDS must independently verify active request authority;
worker-supplied fencing fields alone are not authorization. Reservation/finish
fencing remains an integration gate before production activation.

Production still requires the reviewed source-reader/compiler, existing native
runner class and approved roots, request-type/domain mapping, service identity,
PDS authority binding, and existing scratch cleanup binding. A factory with
injected test objects is not proof of production readiness. Additional tests
`tests.test_image_native` use a real synthetic subprocess, and
`tests.test_image_music_pds` exercise staging/publication boundary fixtures.

## Formal intake and verified publication integration

`adapters.music_intake.MusicIntake` installs its typed payload table only by an
explicit schema call. It accepts the original HQ DTO in a Common transaction,
with a separate immutable target/revision binding, source `audio.music`, request
type `audio.music.generate`, schema `audio-music-hq.v1`, domain `audio.music`.
The operating Common type allowlist has NOT been changed. An unsupported type
rejects and rolls back; a reviewed deployment migration remains required.
No historic AudioForge records or jobs are copied.

`PortraitSource` preserves the precompiled official portrait prompt and rechecks
PDS context revision/character/source/category hashes. `PortraitContextReader`
uses existing guarded PDS read APIs, and references use PDS asset downloads.
Only staged reference paths and their derived manifest/descriptor hashes change;
original request DTO and source-content hashes remain intact. Existing context
variants must match exactly or fail closed, not silently re-plan.

The media publish body includes the complete Claim for service-side authority
verification. PDS uses the Request service reservation/finish API and immutable
Filex receipts. Worker readback checks publication hash, receipt hash and output
bytes. `media_publication_verifier` is the Request-service completion callback;
it independently reads PDS receipt and bytes and provides `reservedReceipt` for
comparison against the finished reservation, rather than copying the ledger.

Additional isolated tests:
- `test_music_intake`: actual in-memory Common accept, DTO preservation,
  idempotency/target conflict, and unsupported deployment rollback.
- `test_image_native_existing`: actual existing native runner + new CLI +
  synthetic executable, real event/argument/output proof checks; scratch
  registration explicitly mocked to prevent touching operating state.
- `test_image_music_pds_http`: actual worker client → PDS HTTP → Filex HTTP,
  plus new music Common intake → live claim → Request authority HTTP reservation
  and finish → PDS verified readback → Common completion, with exact replay.

Set NGS_RUNTIME_SOURCE, NGS_NATIVE_SOURCE and MEDIA_PDS_SOURCE to reviewed source
checkouts to run the opt-in tests. All databases, artifacts, tokens and ports are
isolated fixtures. Model inference, real native tool availability, authenticated
owner UI intake, and operating deployment selectors remain outside these tests.

### Read-only publication reconciliation

Media workspaces persist and fsync the exact publication intent before POST.
The same intent may be observed repeatedly; a changed intent is rejected.
Service-only `recover(claim)` validates the original full Claim, target/source
binding, publication hash, PDS receipt and asset bytes. It does not invoke a
backend, POST again, clean a workspace, or infer success from a local file.
Missing receipt returns no result. Authorization, confirmed execution stop and
finalization ordering remain the reconciliation supervisor's responsibility.
If cleanup already removed the intent, recovery needs the Request service's
finished reservation plus independently re-read PDS evidence; this workspace
method does not reconstruct a missing intent or create a new attempt.

The separate service-only `resume_publication(claim, expected_publication_hash)`
can re-POST exactly the durable intent after the supervisor has proven an existing
prepared/committed reservation and stopped execution. It rejects absent intent,
changed Claim or expected hash, and bad artifact bytes before posting. It then
requires PDS receipt/byte readback. This is publication resumption only, never
model regeneration, new request admission, or a worker-visible general retry.
