# Team setup: one Workers repository, separate runtime and data

This is the private unified Workers repository. AudioForge's generation API,
catalog API, review API and file consumer use one package in `modules/audioforge`
and one launcher. `workers/audio` is the separate pinned NGS music boundary;
installing AudioForge does not configure that boundary or grant NGS permissions.
Use the reviewed public source at https://github.com/pvenus/forge-workers and select an approved revision. No public release or model-license
clearance is implied. LTX migration and new video/Wan features remain on hold.

## Keep four things separate

- **Git checkout:** source only, no credentials, queues, DBs or weights.
- **Service environment:** a private Python venv outside the checkout, on the main
  SSD for always-on operation. This contains lightweight API dependencies, not models.
- **Audio data directory:** dedicated, outside the checkout. It owns `queue.sqlite3`,
  `artifacts`, `bgm-requests`, `bgm-request-state` and `reviews`. Preserve the entire
  existing directory when relocating, including request IDs and recovery history.
- **Model environments/weights:** separately provisioned and licensed. ACE's model
  Python is distinct from the service Python. Do not copy another owner's private
  operational data or credentials as a new-machine template.

## Install code only

### Create a new private workspace first

The initializer prepares AudioForge and ImageForge directories and environment
files together. It does not install packages, create a DB, download weights,
start listeners or enable consumers. Use a **new** directory outside this Git
checkout, with an existing owner-writable parent:

```sh
python3.12 -B scripts/init-workspace.py --root "$HOME/Workspaces/Workers-new"
python3.12 -B scripts/init-workspace.py --root "$HOME/Workspaces/Workers-new" --apply
```

The first command only prints a plan. The second exclusively creates the workspace;
it refuses an existing directory instead of merging or overwriting it. A partial
failed initialization is retained for inspection, not deleted or retried in place.
This is for a **new installation**, not a way to migrate the operating workspace.

It creates `config/audioforge.env` and `config/imageforge.env` with owner-only
permissions, separate data/runtime/log roles, and ImageForge's required internal
namespace modes. Its empty legacy inventory means no private legacy resources
were inherited. ACE/review weights, service venvs, identities and NGS bindings
remain separately provisioned. No model terms are accepted or licenses invented.

Review and source only the applicable environment file before the commands below.
For example:

```sh
. "$HOME/Workspaces/Workers-new/config/audioforge.env"
```

The generated ACE path is a planned location, not evidence of installed models.
Do not source a new-machine file over an operating service's preserved settings.

For the preferred XL-SFT + LM4B asset bundle, follow [pinned model setup](MODEL-SETUP.md).
Its default dry-run uses no network, and only explicit `--download` obtains
the official pinned weights/code-sync/notices into a separate new bundle.
That completes artifact preparation only; full ACE source/dependency installation,
checkpoint integration and activation remain distinct. The [LM+XL formal worker](AUDIO-HQ.md)
is implemented in source and synthetic-tested, but not activated or model-tested.
The current Turbo-only service remains unchanged; its smaller no-LM asset bundle
is an explicit compatibility choice, not completion of the preferred quality target.
The 0.6B text encoder is shared by both configurations. This does not install review
GGUFs, legacy Stable Audio or any video model.

The current model adapters target Apple Silicon/macOS. Use Python 3.11+ (the
validated service environment uses 3.12). From the repository root, set
`WORKERS_AUDIO_VENV` to a **new absolute path** outside the checkout and `PYTHON`
to the intended Python interpreter, then run:

```sh
sh scripts/setup-audioforge.sh
```

Export those variables first so the script receives them. The installer creates
a venv, installs `requirements.lock.txt` and the local module, then imports the
file worker. It does not start services, download models or consume requests.
It needs official package-registry access or populated cache. Inspect any existing
venv before reuse; the setup script is not a no-overwrite migration tool.

## Configure without starting

Copy `config/audioforge.env.example` to an owner-private location outside Git and
restrict access. Set `WORKERS_DATA_ROOT` and `ACE_RUNTIME_ROOT` to your actual
absolute directories; neither contains an inherited teammate path. Source the
private copy only after reviewing it. It explicitly selects ACE, not mock.
Preserve any existing DB/artifact overrides instead of applying defaults over them.

With `WORKERS_AUDIO_VENV` still exported, run from the repository root:

```sh
"$WORKERS_AUDIO_VENV/bin/python" scripts/run-audioforge.py generation --check
"$WORKERS_AUDIO_VENV/bin/python" scripts/run-audioforge.py catalog --check
"$WORKERS_AUDIO_VENV/bin/python" scripts/run-audioforge.py review --check
"$WORKERS_AUDIO_VENV/bin/python" scripts/run-audioforge.py file-worker --check
```

These commands inspect source/config only. A passing check does **not** prove
model readiness, licenses, DB compatibility, free ports or authorization. The
launcher forces the same Git package into review subprocesses. Do not run from
an unreviewed or concurrently edited checkout for persistent operation.

## Prerequisites before enabling consumption

- **ACE:** an approved existing runtime with `source/.venv/bin/python`, its pinned
  ACE source/checkpoints and offline cache. The adapter expects these under
  `AUDIOFORGE_ACESTEP_ROOT`; service pip installation does not supply them.
  Preserve model pins/licenses and native memory protection. Do not download or
  accept model terms implicitly during startup.
- **Review:** the current evaluator uses the Homebrew-installed `llama-mtmd-cli`
  at its code-defined location and macOS memory tools. It requires
  `models/qwen25-omni-7b-q4/Qwen2.5-Omni-7B-Q4_K_M.gguf` and
  `models/qwen25-omni-7b-q4/mmproj-Qwen2.5-Omni-7B-Q8_0.gguf` under the audio data
  directory. This layout is currently fixed: relocating those review weights
  independently requires a service change, not an invented environment variable.
- **Legacy Stable Audio:** if preserving this existing route, keep its separately
  approved MLX directory/executable, weights and bindings. ACE selection alone
  is not proof that every historical model request is supported.
- Review the existing configuration, formal request admission and upstream NGS
  identity/approval wiring. Keep unsupported/invalid requests blocked. Do not
  requeue uncertain work, change retry authorization, or fabricate trial requests
  in a production queue just to prove setup works.

## Activation handoff

The roles remain local-only: generation **8765**, review **8766**, catalog **8767**;
file-worker polls the configured generation API. Before an operator starts them,
confirm the ports are unused or perform an explicit handover, and stop/drain the
old consumer. Run **one consumer per queue**. Do not run a foreground consumer
beside an existing LaunchAgent. Preserve DB/artifact paths, request/state files,
model directories, environment and poll interval during a code/venv switch.
The launcher supports `--interval` but not custom request/state CLI overrides.

Actual start commands and isolated tests are in
[AudioForge README](../modules/audioforge/README.md). Do not remove `--check`
from the commands above until the intended operator has completed this handoff.
No LaunchAgent, request, DB or running service is created by this document.

## ImageForge is a separate lightweight service

ImageForge is included in this same repository but uses a separate service venv
and data namespace. Its HTTP operations require Pillow/NumPy, not generation
weights. Follow the package's [installation instructions](../modules/imageforge/README.md)
with a new venv outside the checkout, on the main SSD. The MCP extra is separate
from the HTTP service. Do not start a second server beside the existing port 8000
LaunchAgent.

For a workspace created by the initializer, install code into its **new** venv:

```sh
. "$HOME/Workspaces/Workers-new/config/imageforge.env"
python3.12 -m venv "$WORKERS_IMAGE_VENV"
"$WORKERS_IMAGE_VENV/bin/python" -m pip install -r modules/imageforge/requirements.http.lock.txt
"$WORKERS_IMAGE_VENV/bin/python" -m pip install --no-deps modules/imageforge
```

These commands install HTTP code only. The initializer has already provisioned
the empty internal namespace; no current owner's legacy data is needed or copied.
Starting the server remains a separate, explicit ownership handoff.

For the internal layout, explicitly set `IMAGEFORGE_STORAGE_MODE=internal` and
choose `IMAGEFORGE_STORAGE_BASE` outside the checkout. The intended role is
`$HOME/Workspaces/Workers/data/imageforge`; its shared namespace is
`resources/v1/imageforge`. Set `IMAGEFORGE_LEGACY_ROOT` and
`IMAGEFORGE_LEGACY_INVENTORY` only to the intended owner's reviewed internal
legacy directory and inventory. Existing private legacy files are not a template
to distribute to new teammates. The module README specifies required directory
modes and the owner/ACL/symlink/device checks. Do not weaken those checks to make
an unprovisioned or copied layout start.

Without explicit internal mode the package retains the old external-storage
branch. The current operational launcher selects internal mode and checks the
internal storage contract; a bare package install does not install that launcher,
provision private data or authorize publication. Verify `/api/health`, not
`/health`, after an explicit service handover. LTX and new video functionality
remain excluded from this setup.
