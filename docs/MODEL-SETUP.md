# Pinned model bundle preparation

This is the model-download step for a **new** unified Workers installation, not
an operating-service migration or a one-command model runtime installer. The
script is standard-library Python, defaults to an offline plan and never imports
Torch/MLX, runs generation, changes a service or selects a model for NGS.

**Preferred music target: XL-SFT + 5Hz LM4B, the previously used high-quality
4B-class + 4B-class pipeline.** The smaller no-LM Turbo profile prepared first
is only a compatibility baseline, not completion of this quality goal.
Qwen3-Embedding-0.6B is the shared **text encoder**, not a standalone music
generator and not a substitute for the separate 4B planning LM.

## Current model registry

`manifests/model-assets.v1.json` records scope, official sources, immutable
revisions, artifact sizes, SHA-256 and available Git blob hashes.

| Profile | Setup status |
| --- | --- |
| `acestep-xl-sft-lm4b` | **Default asset plan**: XL-SFT + LM4B + shared text encoder/VAE; formal generation adapter not yet implemented |
| `acestep-turbo` | Explicit compatibility baseline: smaller Turbo + text encoder/VAE, no planning LM; not the preferred final music target |
| `imageforge-http` | No model download: current HTTP image operations use Pillow/NumPy |
| `qwen25-omni-review` | Held: existing GGUF review code records conflicting license metadata/provenance |
| `stable-audio-legacy` | Held: independent runtime and license/eligibility need review |
| `video` | Excluded: LTX migration and new video/Wan features remain on hold |

This manifest is installation metadata, not the operational model/request
registry and not an authorization grant. Download support does not enable LM/XL
requests in the operating service. The current instrument-only adapter still
allows only Turbo with thinking disabled. Its limits and guards are unchanged.
ImageForge is not an image-generation model installer.

## Plan first, then explicitly download

After the [workspace initializer](TEAM-SETUP.md), choose a **new bundle directory**
whose parent exists. Do not point at an operating model directory.

```sh
python3.12 -B scripts/model-assets.py --list
python3.12 -B scripts/model-assets.py \
  --profile acestep-xl-sft-lm4b \
  --destination "$HOME/Workspaces/Workers-new/runtime/ace-hq-bundle"
```

The default prints the exact model/code pins, licenses, size and destination.
It creates nothing and uses no network. The default high-quality bundle has
**44 files, 29,924,538,685 download bytes** (about 27.87 GiB), including notices
and three code-sync files. Allow at least another 512 MiB free for this transfer. Python dependencies,
the full source checkout, model cache and inference memory are additional.

Only after reviewing the plan and license notes, explicitly request the download:

```sh
python3.12 -B scripts/model-assets.py \
  --profile acestep-xl-sft-lm4b \
  --destination "$HOME/Workspaces/Workers-new/runtime/ace-hq-bundle" \
  --download
```

The downloader uses the exact official Hugging Face/GitHub URLs and permits only
their HTTPS artifact redirects. It sends no Hugging Face token, account cookies
or proxy credentials and has no alternate-mirror fallback. A gated/denied origin
stops the transfer; this command does not accept additional terms or bypass it.
File size and SHA-256 are checked before a partial file is promoted. Non-LFS
model files also verify their published Git blob hash. A download lock prevents
two cooperating runs from writing the same bundle.

If interrupted, inspect the bundle and then explicitly resume the **same exact**
manifest/profile with `--download --resume`. Verified completed files are not
downloaded again; partial files require an exact HTTP Range response. Unsupported
Range, changed source/hash or a conflicting receipt stops without truncating
existing bytes. There is no automatic retry. Corrupt files remain for inspection;
the helper does not delete/repair them or guess which original to replace.

To verify an existing bundle without network or changing configuration:

```sh
python3.12 -B scripts/model-assets.py \
  --profile acestep-xl-sft-lm4b \
  --destination "$HOME/Workspaces/Workers-new/runtime/ace-hq-bundle" \
  --verify
```

This reads all artifact bytes to check hashes. A success is **bundle verified**,
not a model-load, inference, content-acceptance or operational-readiness result.

## Runtime compatibility, not activation

The preferred bundle preserves the model pins found in prior successful use:

- Renderer `ACE-Step/acestep-v15-xl-sft` revision
  `d06de46b4622f781cf07f4a013a67d591ca52819`.
- Planner `ACE-Step/acestep-5Hz-lm-4B` revision
  `0a3ec94b557aea7d508da38b31cfe7341f6ff737`.
- Shared text encoder/VAE from `ACE-Step/Ace-Step1.5` revision
  `19671f406d603126926c1b7e2adc169acbcade22`.
- ACE runtime source revision `ca1e85fe9430179831e6bc6be790c332190a3866`.

The original HF Python files are preserved under `upstream-original/`. Under
`checkpoints/acestep-v15-xl-sft`, three corresponding files come from the pinned
official runtime source instead. This matches the upstream downloader's model
code sync and the observed existing installation, rather than silently treating
HF-only files as identical. Both versions are hashed and attributable.

The synced files import `acestep.models.common`: **the full pinned runtime source
and its own Python 3.11/3.12 environment are still required**. The service venv
does not contain those dependencies. The existing adapter expects this final
checkpoint layout under `$AUDIOFORGE_ACESTEP_ROOT/source/checkpoints`; the bundle
command never copies there, replaces an existing checkpoint or modifies that
environment variable. Provision/install that separate runtime, reconcile its
checkpoint directory, and perform the explicit activation handoff before using
real requests. Do not symlink it over someone else's live data as a shortcut.

The high-quality formal worker is now implemented in source, **not activated**.
[HQ request contract](AUDIO-HQ.md) describes the explicit new profile. It persists
LM audio-code/metadata provenance, waits for that child process to exit, then
starts XL-SFT in a separate child. MLX BF16 LM and MPS FP32 XL-SFT retain the
historical 50-step / guidance 7 / shift 3 settings. MLX DiT/VAE are disabled;
direct model placement avoids deliberate duplicate CPU/MPS residency.

Validation to date is synthetic subprocess, request/queue and pin-boundary tests
only. No model was loaded, downloaded or used to generate content in this change.
`currentGenerationAdapterSupported:false` records the **unchanged operating
binding**, while `sourceGenerationAdapterImplemented:true` records the new code.
The new profile needs its own explicit activation and runtime integrity check.
Existing Turbo requests, duration limits, approvals and retries are not upgraded
implicitly. `thinking=true` or downloading weights does not activate the pipeline.

Official model names describe a 4B-class + 4B-class combination. The pinned HF
safetensors metadata actually reports **4,987,310,726 F32 XL parameters** and
**4,189,554,176 BF16 LM parameters**. XL's README labels a size row “bf16”; do not
mistake that wording for a BF16 artifact. The manifest records the actual dtype.

### Historical execution and memory limits

Retained settings/audio-code hashes and existing WAV headers identify 18 successful
XL-SFT+LM4B pairs, including 14 FP32 renderer runs. A separate XL-Turbo+LM4B
comparison and two LM1.7B comparisons are not counted as that target. For example:

| Output duration | Separate LM time | XL-SFT time | Sampled swap increase |
| --- | ---: | ---: | ---: |
| 40 seconds | 26.19 s | 36.24 s | 0 MiB |
| 72 seconds | 28.20 s | 62.48 s | 0 MiB |
| 192 seconds | 38.26 s | 171.27 s | 0 MiB |

These are historical local measurements, not fresh load tests or speed promises.
Representative LM peak RSS was about 8.94–8.95 GiB; this is **not total unified/GPU
memory**. The lowest sampled free+inactive+speculative estimate in those FP32
runs was 24.19 GiB, also not a reliable total model-allocation measure. Sequential
loading, actual memory pressure and swap monitoring remain required. A 96GB
machine's installed RAM alone does not certify readiness or safe concurrency.
Existing weights were not rehashed in this review; one SFT shard lacks its local
cache metadata, so installed-file integrity is not asserted from size alone.

### Compatibility baseline, explicitly selected

`--profile acestep-turbo` still plans/downloads the prior **21-file,
6,336,813,493-byte** smaller Turbo bundle into its own new destination. It has no
4B planning LM and is not the preferred quality target. This keeps the currently
implemented Turbo adapter reproducible without presenting it as final music setup.
Do not install both bundles by default; their shared encoder/VAE would be duplicated.

No runtime installation or real download was performed when implementing this
helper. Its transfer/resume/hash behavior was tested with small synthetic bytes;
the real artifact pins were checked against official metadata and prior
installation evidence, without re-reading the large weights.

## License provenance

- [ACE code LICENSE](https://github.com/ace-step/ACE-Step-1.5/blob/ca1e85fe9430179831e6bc6be790c332190a3866/LICENSE)
  is MIT. Retain its copyright and permission notice for covered redistribution.
- The [pinned ACE model card](https://huggingface.co/ACE-Step/Ace-Step1.5/blob/19671f406d603126926c1b7e2adc169acbcade22/README.md)
  declares MIT and commercial-use suitability for generated music. The model
  repository has no separate LICENSE file at this pin. VAE's recorded basis is
  this aggregate card declaration, not an independently found VAE license.
- [Qwen3-Embedding-0.6B's upstream card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md)
  declares Apache-2.0; its LICENSE path returned 404 at that pin. Preserve the
  upstream attribution/notice requirements rather than relicense the encoder
  solely from ACE's aggregate MIT tag. Exact upstream-versus-bundled weight
  equivalence was not independently established.
- [Pinned XL-SFT](https://huggingface.co/ACE-Step/acestep-v15-xl-sft/blob/d06de46b4622f781cf07f4a013a67d591ca52819/README.md)
  and [LM4B](https://huggingface.co/ACE-Step/acestep-5Hz-lm-4B/blob/0a3ec94b557aea7d508da38b31cfe7341f6ff737/README.md)
  cards declare MIT; neither tree has a separate LICENSE. Their pinned READMEs
  are retained with the checkpoint files.
- LM4B identifies Qwen3-4B as its base. Its [upstream Apache-2.0 LICENSE](https://huggingface.co/Qwen/Qwen3-4B/blob/1cfa9a7208912126459214e8b04321603b3df60c/LICENSE)
  and README are retained separately. These are pinned upstream notices, not
  proof of the exact base revision used to train the ACE LM.

The three shared notices plus Qwen3-4B README/LICENSE are included in `notices/`
for the high-quality bundle, in addition to its two checkpoint READMEs.
The manifest does not certify all weights' provenance, grant blanket commercial
rights, or clear the Workers repository/public redistribution. Model licensing
also does not guarantee that a particular generated piece avoids third-party
rights. No license acceptance or new project license is recorded by this helper.
