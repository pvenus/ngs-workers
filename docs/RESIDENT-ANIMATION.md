# Animation resident candidate (not activated)

`AnimationBackend` implements the common load/unload/execute port and ports the current production Wan conditioning/sample/decode graph to the Workers repository. No direct worker CLI, request database, or canonical file writes are used. The Request service must atomically grant `animation.generate` and video-global capacity 1 across all callers. Runtime renews the lease while execution runs; cancellation is observed during polling/encoding. Every inference/encoding process group must be joined before slot release. Unconfirmed stop raises `ExecutionStopUnconfirmed`, not a safe failure.

## Residency and memory

Service polling is resident. load() validates an owner-selected installed engine; execute lazily starts its own loopback-only offline Comfy process after PDS input validation. Because there is no separate idle model-residency budget, the engine is stopped even after successful jobs before returning its global grant. Idle unload is idempotent. This deliberately does NOT claim cross-request model caching. Existing 48 GiB start, 8 GiB available floor, 4 GiB swap-growth and 3000-second limits remain. Conditioning/sample phase unloads remain. True lazy loaded model retention needs server-side residency ownership first.

## Required PDS stage contract

PDS stages `animation-job.json` in its attempt workspace with exact keys:
- claim: requestId, attemptId, fencingToken, expectedRevision, parametersHash (canonical UTF-8 sorted compact JSON SHA256), inputRefs.
- job: frameCount, fps, appearancePrompt, negativePrompt, motionPrompt, referenceType, framingPolicy. PDS must derive prompts and pose/motion selection from the formal request and pinned source, not accept arbitrary caller paths.
- inputs: reference and motion, each `{path,sha256,ref}`. Paths are workspace-relative; refs must belong to claim.input_refs; bytes must match SHA256.

Generated engine inputs, outputs, embeddings and temporary files stay inside the attempt workspace. Original installed models/source are read-only. Comfy bootstrap checks pinned upstream hashes. Publication remains the PDS port's responsibility, including source version/CAS, media proof, all expected directions and readback. Animation PDS adapter is implemented below; production service wiring is not activated. A source change must reject publication, never silently publish to another revision.

## Recovery and validation limits

A saved completed result can be verified by artifact hash. A started job without completion is never automatically re-executed by this engine. Sample outcome ambiguity needs official Request/PDS reconciliation; no fake receipt or synthetic video is produced. Visual cycle correctness is not claimed by a successful encode.

8 isolated tests cover lifecycle, input/fence/hash/path rejection, missing global grant, cancel, output escape, stop-unknown propagation, actual harmless process join and fresh conditioning graph. No Wan model was loaded, no video generated, and no production service or request changed. Full inference, engine API compatibility, memory pressure, PDS publication and consumer cutover remain required activation gates.

## Follow-up residency and PDS implementation

AnimationBackend now supports `set_model_lease(lease)` with an explicitly configured canonical `residency_root`. The common runtime acquires/renews this lease independently of request execution. Owned Comfy scratch is created under this separate root; job input bytes are validated before copying there, and generated media/evidence are copied into the PDS attempt workspace. On success the idle process and its last loaded VAE can remain resident; no sampling model persistence claim is made because the existing phase-level unload policy remains. `unload()` proves process-group stop, removes only its generated session directory, and then permits the common runtime to release the memory lease. An idle proof records the completed output hash, owned PID and empty queue before request-slot release. Server-side verification is still required, not trust in this JSON alone.

The new `AnimationWorkspace` stages one direction from actual PDS current NPC, selected pose view, scoped worker context and registered motion source. PDS source revision, context hashes, input references, direction-specific pose lineage and exact preset bytes are checked. It publishes through `AnimationPublicationClient` to the existing animation endpoint, validates the immutable receipt and re-reads it. Publication allows unrelated direction/frame results only: all non-output NPC data, the target slot, pose, context and motion remain pinned. It then uses fresh source-version/character CAS. Eight-way composition is an upstream sequence of eight formal claims, not an unbounded local batch.

Additional tests: retained-session idle cleanup and unrelated-file preservation; guarded publication/replay and receipt mismatch; opt-in real PDS Filex fixture publication/replay. The process/graph code is not GPU-tested. Operational startup still requires service-owned residency and execution verification, actual PDS workspace/config wiring, and complete request lifecycle integration. No production transition occurred.
