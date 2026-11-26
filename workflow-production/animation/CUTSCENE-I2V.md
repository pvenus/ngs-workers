# Official native PNG sequence cinematic workflow

New sequence requests use `cinematic.sequence.generate` /
`ngs.cinematic-sequence-generate.v1`. They are not MP4 requests.

- Exact input: `{projectId,sourceSha256,motionPrompt,playback}` plus request
  schema. The playback metadata preserves `durationFrames` (1–1440), a speed
  curve `[{frame,rate}]` (rate 0–4), and a camera curve
  `[{frame,zoom,x,y}]` (zoom 1–3, normalized x/y 0–1). Curves must start at
  0 and have strictly increasing integer frames below the playback duration.
- The existing exclusive Wan slot still renders **45 RGB PNGs at the native
  24fps grid**, independent of the playback metadata. No FFmpeg, crop,
  interpolation, matting, resizing, repack, or PNG re-encoding happens in
  sequence publication. Display-time controls are stored, not burned in.
- Exact worker task: `cinematic_sequence_render` /
  `cinematic.sequence.generate.render`. It checks the full claim and source
  SHA-256, validates every native PNG's byte length, CRC, RGB shape and hash,
  then stages each PNG in order via the private PDS workflow sequence port.
  Only the manifest's atomic visibility acknowledgment allows worker
  completion at `binari/cinematics/sequences/<officialId>/manifest.json`.
- Identical same-claim frame/manifest writes can retry once in-process on
  transport uncertainty; unresolved publication stays fenced in progress
  for reconciliation rather than re-running inference.
- Separate private readiness `cinematic-sequence-animation-ready.json` checks
  the pinned A14B model (not FFmpeg), updated every 30 seconds. Optional
  owner binding `WORKFLOW_CINEMATIC_SEQUENCE_ANIMATION_READY_FILE` must use the
  same filename. Model-stop uncertainty invalidates the shared slot and
  all readiness proofs.

## Legacy v1 MP4

Historical requests and their already queued v1 jobs retain their own
original format, readiness and publication routes.

A full-scene artifact path, not a prop effect or NPC animation. The existing
resident worker reads one approved storyboard PNG and its motion brief and uses
the existing exclusive single-model runtime. The adapter returns exactly one silent H.264 MP4
(`video/mp4`), with input SHA-256 and the result's SHA-256, dimensions, FPS and
frame count. The currently pinned model yields 45 frames at 24 fps (1.875 s)
with its usual resolution limits. The adapter consumes the RGB frames directly:
no chroma key, segmentation, pixel conversion, forced resize or prop package.

```js
const render = createCutsceneRenderer({renderI2V: job => modelSlot.runI2V(job)});
const artifact = await render({source: approvedPng, motionPrompt, signal});
// The workflow caller must publish artifact.bytes via the usual media authority.
```

For the entrance to the consciousness, a first prompt can be restrained:
“The father's hands unfold the child's jacket. Its fabric shifts gently.
After a brief pause, the room's ink-wash edges soften. Slow camera push-in.
Keep the composition and the painted ink texture.” The call of the child's
name is an editorial/audio cue, not something this silent clip generates. The
candle is a recurring visual motif, not the action that opens the path.

## Resident wiring

- Request: `cinematic.generate` / `ngs.cinematic-generate.v1`, project `binari`,
  immutable approved `sourceSha256`, and nonempty `motionPrompt`.
- Planner: exactly one animation worker step `cinematic_render` with type
  `cinematic.generate.render`, no dependency. This workflow does not claim
  any prop/postprocess task and does not start another daemon.
- Each PNG read and MP4 publication is authorized by the exact official claim
  at PDS `/v1/cinematics/ownership/workflow`; the resident verifies the source
  bytes against the registered hash. It does not read a user URL or direct
  Filex path. The worker marks completion only after PDS acknowledges its
  exact result ref `binari/cinematics/results/<officialRequestId>.json`.
  A lost publication acknowledgement leaves a reconcilable in-progress
  claim instead of silently scheduling a second generation.
- The worker only writes the private cinematic admission proof after checking
  the shared Wan I2V-A14B Q4_K_M runtime and the trusted local ffmpeg/ffprobe tools.
  The default is `cinematic-animation-ready.json` next to the prop proof, or
  the same basename under `WORKFLOW_CINEMATIC_ANIMATION_READY_FILE`. Refresh
  is 30 seconds and model ownership uncertainty invalidates both proofs.
  The earlier prop-only proof does not enable cinematic admission.

Unit/transport tests use a scratch planner DB and substituted model/PDS.
The optional encoder tests use the installed host tools and verify full-frame
RGB is retained in a playable 45-frame MP4, with coded 24 fps and no audio.

```sh
NGS_TEST_CUTSCENE_ENCODER=1 node --test cinematic-worker.test.mjs cutscene-i2v.test.mjs prop-animation-readiness.test.mjs
```

Scratch end-to-end integration using the staged BackendServices checkout
(real RS, Planner, PDS, Filex writer and FFmpeg; only model response substituted):

```sh
svc=/absolute/staged/path/to/BackendServices
PYTHONPATH="$svc/data-service:$svc/filex-writer" NGS_CINEMATIC_SERVICES_ROOT="$svc" \
  python3 -B cinematic-resident-chain.fixture.py -v
```

## Looping background animation: mandatory 2× postprocess

A `background.animation.workflow` variant uses the pinned native Wan PNG
sequence solely as transient input to SeedVR2 7B FP16. The resident releases
the owned Wan/I2V model group before launching the offline SeedVR2 Comfy group
in the same exclusive model slot. The approved graph consumes all 45 frames
in order and applies Lab color correction. Native PNGs retain the exact
frame count, order, aspect ratio, and playback metadata. The upscale output
must be exactly 2× both dimensions and only those final 45 RGB frames can go
through PDS publication. There is no Lanczos-only or original-size fallback.
The internal scale node prepares SeedVR2 input and is not itself a deliverable.

SeedVR2 reuses the explicit Wan I2V Comfy root and Python binding. The default
local weight directory is `~/Workspaces/Workers/runtime/seedvr2-fp16-staging/models`,
or the host may set an absolute, privately provisioned
`WORKFLOW_SEEDVR2_MODELS_ROOT`. Runtime SeedVR2 nodes and both model weight
digests are pinned; MPS is required. Scratch frames disappear with the
temporary job directory after a confirmed model exit. This postprocess is
not applied to regular cinematic sequences, NPCs, or props.

Pixel background variant: validate Wan → validate SeedVR2 2× → correct all frames to the pinned still's shared palette without resizing → validate and publish. A correction error stages no frames. Cinematic backgrounds publish the verified SeedVR2 output without palette correction.
