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
