# Formal one-result BGM worker

The only audio model configured for this worker is ACE-Step 1.5 XL-SFT + LM 4B HQ. No Turbo, effect, or TTS fallback. Accepts
one official `audio.generate` / `audio_generate` / `audio` task, acknowledged by
Request Service and bound to the exact Planner payload, whose only accepted
kind is `bgm`. Fixed duration is 10–30 seconds; `durationSeconds: null`
activates the HQ model’s own duration plan, with optional `text` for exact lyrics.
Optional `targetDurationSeconds: 60` enables full-length mode for automatic
duration. The HQ provider restricts the model's duration vocabulary before
composition to 60 seconds through the existing maximum (120 seconds or the
runtime's lower hardware bound). The LM still selects the length. Up to five
plans are permitted, but only a qualifying plan is rendered; a short plan or
WAV is never published as a full-length result. Plain automatic and fixed
requests keep their existing duration behavior. The model’s output must fit
the bounded 10–120s WAV contract. Exact Planner audio envelope from `e5db988`.

The CLI accepts explicit owner-managed paths and config:
`audio-cli.mjs --run --db <private workflow sqlite> --adapter-module <trusted absolute module> --adapter-config <trusted absolute binding>`.
Trusted adapter module is `adapters/load.mjs`, with a 0600 config of exact shape `{"schemaVersion":"workflow.bgm-adapter-binding.v1","hqBinding":"<absolute verified HQ file>"}`. HQ auto provider: `workers-hq-auto-2a29687f0724`. Verify the separate installed binding with model capability preflight before configuring a resident process. Fixed requests keep the existing instrumental default; only auto requests may supply a BGM vocal cue.

## Workflow capability: BGM caption preparation
The existing audio worker still owns one `audio_generate` task and one final
WAV. It receives the approved brief and the active sound Master Concept from
PDS. Before starting ACE-Step, it invokes the installed Codex CLI in read-only,
ephemeral mode, using only the concept title/body and the request title/brief,
duration choice, and existing vocal mode. Prompt authoring rules are maintained
in `bgm-caption.mjs`: one English narrative paragraph, melody and instrumental
roles, arrangement and dynamics, musical arc and resolution or loop, and
limited compatible additions where the brief leaves room. The output schema
accepts only a caption, at most 2,000 characters. Source Markdown is never
sent directly to ACE-Step, including on timeout or invalid output.

The caption is shared by the composer and renderer. Approved lyrics/humming
and duration are forwarded unchanged, and this capability does not infer or
change structured model settings. A CLI failure is reported as
`AUDIO_CAPTION_FAILED` (phase `caption`) and uses the common task retry path.
No new task type, queue, RS/Planner schema, or PDS storage contract is needed.

## PDS / Filex contract

- GET loopback PDS port 8030 `/v1/audio/ownership/workflow-generate` receives
  `{enabled:true,schemaVersion:"workflow.audio-generate-write.v1"}`.
- POST `/read` under that path receives exact `{claim}`, with claim
  `{officialRequestId,taskId,projectId,payloadSha256,claimStamp}`. PDS independently
  checks the running row and the authoritative active category in Master Concept
  `group.sound`, returns `{conceptId,sectionSha256,title,content}` (content max
  256 KiB). No archived or alternative category may substitute.
- The adapter returns exact `{wav,bpm,bpmBasis}`. A model BPM is integral 30–300
  verified as a same-job generation condition before forwarding; it is **not**
  independently measured from the WAV. Without trustworthy BPM use
  `{bpm:null,bpmBasis:"unavailable"}`. No guessing from prompt, title, or filenames.
- POST base `{claim,source:{conceptId,sectionSha256},artifact:{mimeType:"audio/wav",sha256,bytes,base64,bpm}}`.
  PDS/Filex independently verify exact claim, source and single PCM16 RIFF format/data.
  Filex stores `audio.wav` and `metadata.json` under
  `canonical-files/binari/audio/<officialRequestId>/`, deriving true duration from
  WAV samples and `bpmBasis` from the verified reporter value. Worker neither
  touches Filex directly nor writes to NGS. Only exact
  `{ok:true,resultRef:"canonical-files/binari/audio/<officialRequestId>/metadata.json"}`
  completes the claimed task.
- Only explicit HTTP 422 `{error:"WORKFLOW_AUDIO_REJECTED_BEFORE_WRITE"}` is a
  definite post-send failure. An unknown outcome keeps the task running for
  reconciliation and doesn't generate a replacement. Definite failures follow
  shared retry rules: 10 minutes, cap 3, increment only on successful exact claim.
  Do not reuse the old media reservation/receipt workflow.

## Model-free verification

`node --test` uses temporary independent Planner DBs and fixture WAVs. It never
loads production data or starts a model. This is not confirmation of readiness,
deployment, actual generation, or sonic quality.

### Concept and vocal input

`worker-audio` still claims the same single `audio_generate` task. Catalog
requests retain the claim-bound PDS concept read. A direct request
(`conceptId: "direct"`, `conceptText`) reads the PDS claim-bound synthetic
concept; before proceeding, the worker validates the exact registered text,
fixed title and UTF-8 SHA-256. WAV publication retains the same source binding.

Empty vocal input keeps instrumental behavior. A nonempty `text` is the
supplied vocal text and is forwarded without alteration. Alternatively, an
exclusive `lyricsRequest` runs a separate tool-free Codex CLI turn with a
structured Korean-lyrics output contract; those lyrics, not the music caption,
go to ACE-Step. Both vocal modes require automatic duration. Failure to obtain
valid generated lyrics fails the existing task before any rendering or
publication; the worker does not substitute the caption, silently strip lyrics,
or log them.


## Exact existing-song instrumental request

`audio.separate` / `ngs.audio-separate.v1` binds an existing official song ID
and the claimed published WAV SHA-256; it accepts no prompt or exposed tuning.
The resident worker authenticates the separate PDS read and WAV asset using the
exact running claim, validates the bytes/duration and generates once per
worker attempt. Only one new PCM16 WAV is published to the new official request.
The original never appears in a write body. A changed original, equal output
digest, invalid WAV or result differing from the actual input duration by more
than 100 ms is refused without padding, time stretching or text-only fallback.
PDS resolves the new title and lineage; BPM is unmeasured.

A separate CPU process runs Hybrid Demucs from the local pinned TorchAudio
checkpoint. It discards the predicted vocal stem and sums drums, bass and other.
Two staggered 10-second inference grids are blended equally; complementary
raised-cosine fades blend local overlaps within each grid. A shared transient
is preserved, while a spike found in only one grid receives at most half weight.
Each model call and the shared track buffers are bounded; separation may take
about twice the CPU time. The worker outputs
48 kHz stereo PCM16 at the original duration to the nearest output-frame boundary,
without rearrangement, normalization or time stretch. Nothing calls ACE-Step,
its resident GPU process, or the caption/lyrics composer for this request.
Vocal removal quality is model-dependent; audition for residual artifacts.

The deployer must separately enroll the reviewed TorchAudio `hdemucs_high_trained.pt`
original checkpoint at `<HQ runtime>/cache/separation/hdemucs_high_trained.pt`,
with a sibling `pin.json` containing exactly
`{"schemaVersion":"ace-hq-hdemucs-pin.v1","checkpointSha256":"<verified SHA-256>"}`.
The directory is owner-private (0700), both files regular owner-only (0600),
with no symlinks, and the separator checks the model hash before loading tensor
weights from the pinned file. No code downloads package/checkpoint artifacts or
falls back to the original vocal recording. An unprovisioned separator fails
the separation lane explicitly; standard BGM does not require it.
