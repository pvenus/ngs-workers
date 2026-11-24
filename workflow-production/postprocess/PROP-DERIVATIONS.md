# Formal prop derivatives: staged worker connector

`nextPropDerivationFromPath` and `runPropDerivationFromPath` handle the four
postprocess derivative keys in the registered Planner graph, both under
`prop.generate` and `prop.regenerate`, or the two animation-only derivative
keys under `prop.animation.generate`. Neither the connector nor the Pillow
renderer has Filex authority. The worker never selects or claims the final
package publish key or NPC work. No launch/import is active by default.

The installed launcher supplies these private ports; all must independently
verify the current claim and source before reading or staging. The adapter is
not a new worker-facing/public HTTP spec:

- `readPropDerivation(binding,{signal})`: returns exact `source:{taskId,resultRef}`,
  `canvas:{width,height}`, and either a source `media` descriptor (one original
  PNG or an existing detail PNG/APNG), or a pinned RGB-sequence `manifest`.
  All animated stages additionally require a `geometry` proof and exact static
  media for the target lane. On initial/regenerate subtleties this is a completed
  static task's `{taskId,resultRef,media}`; appends use the selected canonical
  package's `{revision,sha256,media}`. RGB source is presently the private tested
  `ngs.prop-native-rgb-sequence.v1` shape in the connector; reconcile it with the
  actual animation producer before enabling a launch. A plain base descriptor is
  `{sha256,bytes,width,height,mediaType:'image/png'}`.
- `fetchPropDerivationAsset(binding,descriptor,{signal,purpose?})`: verified bytes
  only, for the exact source frame/file or (`purpose:'geometry'`) exact static
  geometry asset. No user-provided paths or temporary worker paths cross this
  boundary. The worker also checks hashes and inspects the claimed dimensions.
- `stagePropDerivation(binding,{source,geometry,media,bytes},{signal})`: returns
  exact `{ok:true,resultRef}`, with `resultRef` derived from the registered
  stage and actual canonical PNG/APNG SHA-256. No canonical package pointer is
  written by this operation. A possibly committed callback never auto-retries.

`createPropDerivationRenderer` uses launch-provisioned existing alpha model
pins and cleans owner-private scratch; stills use BiRefNet without a video
tracker, animation uses existing BiRefNet/SAM2. Static detail preserves the
provider canvas. All RGB animation frames are fitted by a **single** geometry
transform to the same verified detail canvas. Pixel cleanup reuses the
deterministic 32-colour palette routine with one palette over the animation,
BOX resize, no dithering and hard alpha; it decodes the exact detail bytes and
runs no model. APNG stores every full RGBA frame independently, with immutable
integer durations and one play, including identical adjacent frames.

The vendored draft Planner topology is the exact stage projection from Services Planner
`0bd1894` (only its canonical/hash helper import is redirected to the
existing worker utility); the prop package module vendors only the identical
PNG/APNG verification functions from the shared NGS contract `5d72456`. No legacy
partial prop edit and no unrelated NGS/browser/pointer routines are imported.
Check those source copies against the reviewed services/NGS release, provision
an actual claim-bound intermediate PDS source/store and connect the resident
launcher before making it runnable. The fixture tests run genuine Pillow and
stub only learned alpha inference and the intermediate PDS transport.
