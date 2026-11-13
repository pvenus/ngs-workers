# AudioForge worker module

Actual operational AudioWorker source, now versioned inside the **single Workers repository**. This is not another Git project and is not an assertion that every worker has migrated. Existing `workers/audio` remains the NGS music boundary; it is separate from this module's file-request consumer.

## Install on a new machine

Python 3.11+ is required. From the Workers repository root:

```sh
PYTHON=python3.12 sh scripts/setup-audioforge.sh
```

The exact currently installed lightweight service dependencies are in `requirements.lock.txt`. This creates `.venvs/audioforge`, installs the module, and verifies import without starting a model or consuming requests. Installation needs package-registry access. Model weights, model runtimes, private credentials, requests and databases are intentionally not in Git.

## Run only after configuring your data and generation service

```sh
export AUDIOFORGE_DATA_DIR="$HOME/Workspaces/Workers/data/audioforge"
export AUDIOFORGE_API_URL=http://127.0.0.1:8765
.venvs/audioforge/bin/python -m audioforge.file_workflow --help
# After an actual generation API and approved request directory are configured:
.venvs/audioforge/bin/python -m audioforge.file_workflow worker --interval 30
```

The worker consumes the existing formal file-request workflow. Do not write trial requests into production, run two consumers against the same queue, or switch operational launchd paths just to test installation. NGS authorization and request registration remain upstream. Missing generation readiness does not authorize bypassing it. Model installation is a separate prerequisite, not performed by this setup script. ACE runtime can be configured via `AUDIOFORGE_ACESTEP_ROOT`; preserve existing pinned model/runner bindings. Review analysis additionally requires llama-mtmd-cli and the configured Qwen Omni weights; it is not a prerequisite for the worker's import test.

## Isolated tests

```sh
cd modules/audioforge
AUDIOFORGE_DATA_DIR="$(mktemp -d)" ../../.venvs/audioforge/bin/python -m unittest discover -s tests -v
```

Tests use fake generation backends and temporary data. `source-snapshot.json` records source hashes at import; it is provenance, not a runtime lock. Existing service definitions and operating data were not moved or restarted as part of source registration. Public publication/license clearance and full fresh-machine model inference remain separate work.

## Explicit Git role launcher

Create the service environment outside source if preferred:

```sh
PYTHON=python3.12 WORKERS_AUDIO_VENV="$HOME/Workspaces/Workers/runtime/audioforge-service/.venv" \
  sh scripts/setup-audioforge.sh
export AUDIOFORGE_DATA_DIR="$HOME/Workspaces/Workers/data/audioforge"
export AUDIOFORGE_BACKEND=ace-step
export AUDIOFORGE_ACESTEP_ROOT=/absolute/path/to/your/already-installed/ace-runtime
export AUDIOFORGE_API_URL=http://127.0.0.1:8765
PY="$HOME/Workspaces/Workers/runtime/audioforge-service/.venv/bin/python"
"$PY" scripts/run-audioforge.py generation --check
```

After the model/runtime and authority prerequisites are satisfied, the existing roles are `generation` (8765), `catalog` (8767), `review` (8766), and `file-worker` (poll interval30seconds). Run one role per foreground process, e.g. `"$PY" scripts/run-audioforge.py catalog`. Ctrl-C stops it. Start generation/catalog before the file worker; never start a second consumer or generation API on an existing queue. A supplied data directory is created/initialized by the original service as needed, not by `--check`. The existing request/approval workflow remains the only content input.

The launcher uses the exact package in this checkout/release rather than silently falling back to an older editable install. It propagates that package selection to file-worker review subprocesses. For migration, preserve existing `AUDIOFORGE_DATA_DIR`, DB/artifact overrides, ACE runtime and Stable Audio MLX directory explicitly. `AUDIOFORGE_STABLE_AUDIO_MLX_DIR` otherwise defaults relative to code and can change when code moves. No service is installed in launchd by these commands.

The operational Git cutover retains the original generation API/contracts and database schema. It does not activate the separate controlled-generation migration, load a model, fix historical static readiness/profile claims, or prove a new audio generation. The new service environment is standalone (not system-site-packages); the model runtime and weights remain separate and unchanged.
