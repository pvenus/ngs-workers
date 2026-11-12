# Pinned model bundle preparation

This is the model-download step for a **new** unified Workers installation, not
an operating-service migration or a one-command model runtime installer. The
script is standard-library Python, defaults to an offline plan and never imports
Torch/MLX, runs generation, changes a service or selects a model for NGS.

## Current model registry

`manifests/model-assets.v1.json` records scope, official sources, immutable
revisions, artifact sizes, SHA-256 and available Git blob hashes.

| Profile | Setup status |
| --- | --- |
| `acestep-turbo` | Explicit pinned download supported: Turbo, Qwen3-Embedding-0.6B, official VAE |
| `imageforge-http` | No model download: current HTTP image operations use Pillow/NumPy |
| `qwen25-omni-review` | Held: existing GGUF review code records conflicting license metadata/provenance |
| `stable-audio-legacy` | Held: independent runtime and license/eligibility need review |
| `video` | Excluded: LTX migration and new video/Wan features remain on hold |

This manifest is installation metadata, not the operational model/request
registry and not an authorization grant. It does not enable extra ACE LM/XL
profiles or make unsupported requests valid. The current instrument-only adapter
uses Turbo with thinking disabled; this bundle intentionally excludes LM 1.7B,
LM 4B and XL checkpoints. ImageForge is not an image-generation model installer.

## Plan first, then explicitly download

After the [workspace initializer](TEAM-SETUP.md), choose a **new bundle directory**
whose parent exists. Do not point at an operating model directory.

```sh
python3.12 -B scripts/model-assets.py --list
python3.12 -B scripts/model-assets.py \
  --profile acestep-turbo \
  --destination "$HOME/Workspaces/Workers-new/runtime/ace-turbo-bundle"
```

The default prints the exact model/code pins, licenses, size and destination.
It creates nothing and uses no network. The selected bundle has **21 files,
6,336,813,493 download bytes** (about 5.90 GiB), including notices and two code-sync
files. Allow at least another 512 MiB free for this transfer. Python dependencies,
the full source checkout, model cache and inference memory are additional.

Only after reviewing the plan and license notes, explicitly request the download:

```sh
python3.12 -B scripts/model-assets.py \
  --profile acestep-turbo \
  --destination "$HOME/Workspaces/Workers-new/runtime/ace-turbo-bundle" \
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
  --profile acestep-turbo \
  --destination "$HOME/Workspaces/Workers-new/runtime/ace-turbo-bundle" \
  --verify
```

This reads all artifact bytes to check hashes. A success is **bundle verified**,
not a model-load, inference, content-acceptance or operational-readiness result.

## Runtime compatibility, not activation

The bundle preserves the current installation's two independent pins:

- Model repository `ACE-Step/Ace-Step1.5` revision
  `19671f406d603126926c1b7e2adc169acbcade22`.
- ACE runtime source revision `ca1e85fe9430179831e6bc6be790c332190a3866`.

The original HF Python files are preserved under `upstream-original/`. Under
`checkpoints/acestep-v15-turbo`, the two corresponding files come from the pinned
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

These three pinned documents are included in `notices/` by the explicit download.
The manifest does not certify all weights' provenance, grant blanket commercial
rights, or clear the Workers repository/public redistribution. Model licensing
also does not guarantee that a particular generated piece avoids third-party
rights. No license acceptance or new project license is recorded by this helper.
