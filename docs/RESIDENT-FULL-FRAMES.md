# Full frame extraction and mandatory pixel processing

Development candidate only. No production worker, model, or service is started.

`FullFrameWorkspace` accepts only the existing full frame request contract, never
half/sourceFrameSet reuse. It pins all eight current NPC animation slots, source
revision, incarnation, and target frame set. Ordered Claim.input_refs are
`pds:asset:<sha256>.mp4` for n, ne, e, se, s, sw, w, nw. Video bytes come only from
the guarded PDS `/assets/animations/` route and must match their SHA IDs.

`FullFrameBackend` requires a model-memory lease for role postprocess. Trusted
configuration supplies python, ffmpeg, ffprobe, module_root, matting_model and
tracking_model. Requests cannot supply binaries, models, paths or memory limits.
The shared Request-service memory authority accounts for alpha alongside video
and music using configured model budgets. Half CPU processing does not request
this alpha model grant. A full/half dispatcher must select by formal scalePreset,
never by inferred data availability.

The migrated pipeline is the existing lossless all-frame extraction plus
BiRefNet matting/SAM2 tracking, with no color-key fallback or synthetic production
output. The subprocess group includes ffprobe, ffmpeg, and alpha-model children;
cancellation joins the group before returning. An unconfirmed exit raises
ExecutionStopUnconfirmed, preventing cleanup/resource release by the runtime.
All weights are offline and paths operator supplied. The service can remain
resident, but this implementation's alpha model is loaded inside a per-direction
child process; it does not claim warm model reuse. The shared memory reservation
remains held throughout the full job (and according to runtime idle unload).

Every decoded frame survives: no frame skipping, resampling, interpolation,
slowing down, repeated first frame, or FPS change. `full` means extraction from
original videos plus alpha plus pixel processing, NOT unscaled final pictures.
The common crop/padding canvas is resized by .5 before a shared 32-color palette
and binary-alpha processing. Source raw PNGs and model-alpha PNGs remain full
resolution as separately referenced artifacts. The final canvas is
ceil(sourceCanvas / 2), which is not necessarily half the original uncropped
video width because crop/padding is shared over all frames and directions.

Before PDS publication, current source slots, incarnation and target are checked
again. Publication uses existing `/v1/npcs/animation-frames/publish` with
scalePreset full and pixelScale .5, Filex source CAS and verified receipt readback.
Other NPCs and original animation slots are preserved. Slot publication remains
separate from mere successful local frame extraction.

Isolated tests `tests.test_frame_full` use an actual synthetic two-frame MP4 and
real ffprobe/ffmpeg, synthetic alpha (no model inference), and real shared pixel
processing. They check sixteen total frames, first/last distinct raw references,
unchanged timestamps/FPS/duration, full-size raw/alpha artifacts, half-size final
canvas, model admission required, half rejection, source tampering/CAS rejection,
actual child timeout/join, and actual PDS publication/receipt into temporary
Filex. The PDS publisher fixture uses direct temporary-authority reads because
its legacy GET dispatcher is not initialized; production reads use FullFrameClient.
Actual model quality and deployed authenticated routing are not proven by these
fixtures and remain activation checks.
