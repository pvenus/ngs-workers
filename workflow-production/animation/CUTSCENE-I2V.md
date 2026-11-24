# Official single-image I2V cinematic workflow

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
  the shared Wan TI2V Q8_0 runtime and the trusted local ffmpeg/ffprobe tools.
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
