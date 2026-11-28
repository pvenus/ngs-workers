# Formal prop derivatives: staged worker connector

`nextPropDerivationFromPath` and `runPropDerivationFromPath` handle the four
postprocess derivative keys in the registered Planner graph, both under
`prop.generate` and `prop.regenerate`, or the two animation-only derivative
keys under `prop.animation.generate`. Neither the connector nor the Pillow
renderer has Filex authority. This derivation worker never selects or claims the final
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
configuration and cleans owner-private scratch. The prop keying engine is
CorridorKey; existing genuinely transparent static source images don't need
a second matte. Static detail preserves the provider canvas. All RGB animation frames are fitted by a **single** geometry
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

## Resident still lane

The same Postprocess resident checks both loopback PDS stage and package
capabilities before considering work. Only ordinary still create/regenerate
is admitted; neither an authored motion request nor a subtly animated create
is accepted even if its task row is already in the local DB. Native provider
uses the existing frame-alpha Python, module root and pinned BiRefNet/SAM
configuration; a still has no tracking invocation. No new process or model
runs at startup. Source and final payloads remain PDS/Filex-only.

The final Postprocess task waits on all four exact success receipts in this
one formal request, asks PDS to construct the package from them, verifies
the request identity, exact predecessor pin and both derivative byte hashes,
and forwards just the manifest. PDS performs authoritative Filex publication
and readback. No ACK/claim mismatch can persist a completed task; after
dispatch an uncertain result is held for reconciliation rather than retried.

On initial or append prop scale requests, each selected percent is a separate
existing-lane `prop.scale.derive` task. The worker claims once, sends only
the exact claim to PDS, and records the hash of the pair descriptor returned
after verified private staging. Each task can retry without touching the
public prop. The final publishing task joins all completed pair hashes and
publishes one revision; it does not derive those scales a second time.
Existing runs with a combined scale publication task remain supported.

Full isolated worker→PDS→Filex fixture (using the staged service tree
containing `filex-writer/test_prop_stage_authority_http.py`):

```sh
cd "$STAGED_BACKEND_SERVICES"
PYTHONPATH=data-service:filex-writer WORKFLOW_PROP_TEST_PYTHON=/path/to/python-with-pillow \
  /path/to/python3 -B "$WORKERS/workflow-production/postprocess/prop-resident-chain.fixture.py" \
  WorkerChain.test_actual_static_worker_chain_create_regenerate
```

The fixture supplies model-free Codex, diffusion and matting results
but executes the production request claims, PNG verification, pixel
derivation, PDS handlers, Filex publication and Planner readback. It does not
open the real Planner database or installed model providers.


A static detail PNG proven to contain the native renderer's genuine RGBA
cutout retains its original visible RGB and entire alpha channel, including
soft edges, instead of being rematted. The proof uses decoded foreground
coverage and transparent background/corners/border; an alpha header alone
does not qualify. An invalid static cutout fails explicitly and is never
rematted. Pixel output still uses the verified detail. The installed matting
model is used only for animated prop detail; NPC matting is unchanged.

## Default chroma pipeline for props

New prop bases are opaque green-screen (RGB) PNGs. The existing asset
construction stages call CorridorKey to make the transparent detail, then
derive the pixel-sized representation from that authorized result. Existing
verified transparent static RGBA bases are passed through instead of re-keyed.
Every prop animation is now framed against green by the animation worker and
uses CorridorKey on its complete RGB sequence. The older candle profile
continues to parse, but it no longer selects a different algorithm. No request
field, schema, Planner stage, or UI option is added. NPC and cinematic media
do not use this prop renderer.

Provision a separate Apple Silicon Python 3.12 environment. The reviewed CLI
source is [corridorkey-v2-mlx](https://github.com/peterteravskis/corridorkey-v2-mlx)
at `74065491ebd2eddea494ab34c15a6d84d7415353`. Install the CLI into that
environment. Provision FP32 profiles 1024×1536 t1 (static) and 768×1280 t1/t4
(animation) from `cdelta/CorridorKey-v2-MLX-fp32` revision
`7bb9a4443d5f1b4b696bcc0547d49ae0ac5e5f7e` and verify against published
SHA256SUMS. Do not commit the CLI or redistribute model files. Read the
CorridorKey model license and third-party notices before integrating into a
commercial product or service.

Set the absolute executable `PROP_CORRIDOR_V2_CLI` and absolute profile
directory `PROP_CORRIDOR_V2_MODEL_ROOT` in `runtime/corridorkey-v2/adapter-config.json`,
next to (not inside) the existing `runtime/frame-alpha` configuration. No model is loaded at worker startup. An unavailable runtime or invalid
output fails this stage explicitly rather than silently falling back to OpenCV
or a foreground recognition model; a new source or motion can be requested.

The bridge verifies input digests, pads without resizing, and submits the
full ordered sequence in one local invocation. It uses the unmodified
CorridorKey matte and foreground: green screen, `--despill-strength 0.10`,
no OpenCV hint or color-specific exception, and four-frame time windows with
stride two for sequences of four or more frames. It checks each 16-bit RGBA
cutout before cropping back. Source dimensions and timings are preserved,
along with the existing APNG packaging and downstream pixel projection.
Worker scratch and intermediates are removed after each run. The worker
never downloads models.
