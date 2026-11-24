# Single-image I2V cutscene adapter

A standalone full-scene artifact path, not a prop effect or NPC animation. Pass
an existing approved storyboard PNG and a motion brief to the resident worker's
existing single-model runtime. The adapter returns exactly one silent H.264 MP4
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

## Integration status

This adapter does not register or claim a new official request, publish to
PDS/Filex, add an NGS control or change the resident launcher. Before exposing
it, register the cutscene request, resolve a project-owned immutable image,
run inside the same model slot, publish a scoped MP4 and its checked metadata
via PDS/Filex, and surface the verified artifact in NGS. The source may not
be an arbitrary URL or filesystem path. Use the same workflow in Local and
Space. Do not add a second runnable model process.

```sh
NGS_TEST_CUTSCENE_ENCODER=1 node --test cutscene-i2v.test.mjs
```
