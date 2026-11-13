# Explicit high-quality music pipeline

Source implementation only. **No operating binding, model activation or real
generation is included in this change.** Existing Turbo requests remain Turbo.

## Versioned request

The explicit model name is `acestep-xl-sft-lm4b`; pipeline version is
`ace-xl-sft-lm4b.v1`. It accepts music only, 10–30 seconds, integer seed
0–2147483647 and the author's prompt unchanged. Settings allow only:

- `instrumental`: boolean, default true. True forbids nonempty lyrics.
- `lyrics`: at most 4000 characters, nonempty when instrumental is false.
- `bpm`: integer 30–300; `keyscale`: nonblank string up to 32 characters.
- `timesignature`: string `2`, `3`, `4`, `6` or `7`.
- `vocal_language`: 1–16 ASCII letters, digits, underscore or hyphen.

No caller-supplied steps, guidance, model path, executable, dtype or quantization.
NGS v2 requests additionally state `profile: acestep-xl-sft-lm4b`,
`generationSettings` and `resultCount: 3`. This means **3 candidates and 6 model
stages**, not a disguised increase to an old request. Existing intent/status is
read before fresh availability checks; polling never regenerates unknown work.
Candidate selection and the existing apply/approval policy are unchanged.

The standalone Audio HTTP route accepts the same explicit model and settings
under its existing snake_case field names. HQ defaults disabled. New requests
receive 503 while disabled; exact saved idempotency replay returns the original
job, conflicts return 409 and invalid raw types/settings return 422 before queue
insertion. This does not redefine the old models' contracts. HQ runs once only;
interrupted running jobs become failed/reconciliation-required, never requeued.

## Sequential execution

1. Verify pinned runtime source and the 36 installed component files. Readiness
   checks metadata/sizes only. Actual generation must additionally verify SHA-256
   bytes, cached only while inode, size, mtime and ctime remain unchanged.
2. Hold the existing shared generation lock across both stages (and both pinned
   lock roles for NGS). Run the MLX BF16 5Hz-LM4B child. Preserve exact prompt,
   settings, audio codes, metadata, request digest and provenance.
3. Wait for successful LM unload **and process exit**. Validate its receipt.
4. Run XL-SFT in a new child: MPS FP32, 50 steps, guidance 7, shift 3, no
   quantization, MLX DiT/VAE disabled, direct MPS model placement.
5. Verify bound receipts, output hash and 48 kHz PCM16 stereo/duration. Publish a
   new output only. Preserve all intermediate/log files, including failed work.

There is no fallback model, automatic generation retry, download or combined
simultaneous LM/XL residency. Per-candidate subprocess deadline is at most
1200 seconds (default 1100); LM at most 240 seconds. Cancel, parent exit,
timeout or monitor failure stops only the owned child process group.

Memory monitoring uses macOS pressure and swap, not an arbitrary free-RAM quota.
Do not start at critical pressure or at least 16 GiB swap. During a stage, stop
after three critical samples (roughly six seconds), 8 GiB new swap, or 16 GiB
total swap. Preserve default MPS allocator protections. Less than 10 GiB disk
space also stops the owned job. Samples are not proof of the true memory peak.

## Installation and activation boundary

First use [MODEL-SETUP.md](MODEL-SETUP.md) to prepare the official bundle. The full
ACE source and Python 3.11/3.12 model environment are separate from the service
venv. `hq_pins.json` is packaged with the service. No large installed weights were
hashed and no model runtime was imported for this code's synthetic checks.

Standalone Audio requires explicit `AUDIOFORGE_ACESTEP_ROOT` and
`AUDIOFORGE_HQ_ENABLED=1`. Merely enabling the flag is not a verified runtime:
`/ready` reports HQ `enabled_unverified`, `runtime_verified:false`. The worker
still denies missing/changed runtime files before inference. Do not enable this
on a live queue as an installation test.

NGS instead requires its separate owner-private mode-0600
`Workers/config/ngs-music-hq.private.json`, schema `private.music-hq-boundary.v1`.
NGS fixes HQ to `Workers/runtime/acestep-hq/source`; the old
`Workers/runtime/acestep/source` remains untouched for v1. Use an independently
installed Python 3.11/3.12 under an owner-trusted path. A group-writable parent of
the resolved interpreter fails the guard; do not loosen the guard or modify
global Homebrew permissions to get past it. The separate runtime must preserve
the original two shared lock roles, not create an independent generation lock.
It has the same binding keys/lock roles as v1, but a distinct exact 13-file pin set:
the runner, resolved venv interpreter, pyvenv.cfg, Workers package init, audio
package init/boundary/legacy_backend/hq_bridge, and AudioForge package
init/backends/hq_contract/hq_backend/hq_pins.json. The runner is
`modules/audioforge/audioforge/ace_hq_runner.py` in the selected immutable release.
No request/environment value can select another NGS executable. Keep the old
`ngs-music.private.json` unchanged. No new private HQ binding was created here.

Remaining deployment work: provision/check the separate pinned runtime, create
the explicit HQ release/binding under owner control, then perform the later
authorized real generation test. The current task intentionally stops before
those operations. No extra licensing acceptance was performed; the official
model/card/code and upstream-notice limits in MODEL-SETUP.md still apply.
