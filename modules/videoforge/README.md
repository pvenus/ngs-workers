# Legacy VideoForge worker source

This module preserves the existing file-request character/video workflow in the unified Workers Git repository. **It is a legacy LTX 2B adapter, not the approved Wan-Animate preset worker.** Do not download LTX weights or advertise Wan support through this module. The user's new animation generation path must use the approved Wan-Animate workflow separately.

## Code/runtime setup

Python 3.12 is the verified legacy runtime. In the Workers repository:

```sh
python3.12 -m venv .venvs/videoforge
.venvs/videoforge/bin/python -m pip install -r modules/videoforge/requirements.legacy-runtime.txt
.venvs/videoforge/bin/python -m pip install --no-deps modules/videoforge
```

The full legacy dependency list records the installed environment, not a recommendation to install or load legacy models. Models and data are excluded. A minimal coordinator installation can use `pip install './modules/videoforge[http]' pillow` without inference/alpha extras; it cannot promise full inference or alpha-processing feature parity.

## Explicit paths before any service start

Set `VIDEOFORGE_JOB_DIR`, `VIDEOFORGE_OUTPUT_DIR`, `VIDEOFORGE_CHARACTER_ROOT`, `VIDEOFORGE_INPUT_ROOT`, `VIDEOFORGE_PROFILE_PATH`, and `VIDEOFORGE_I2V_MODEL_DIR`. The profile must be an absolute path outside installed package data, e.g. this checkout's `profiles/` folder. Installed wheels do not include the legacy profiles directory.

The HTTP producer and both consumers must share the same queue/data authority. Copying consumer data to the internal SSD while HTTP still writes the external SSD splits the queue. Never run a second consumer or replay completed requests to test migration. Keep original data and service definitions until the combined cutover is verified.

## Current migration boundary

Code is registered here, but the operational VideoForge HTTP (8001), i2v worker, character file worker and Forge frontend (8010) still use the external installation. Three historical video jobs are terminal (one completed, two failed), no queued/running jobs or animation requests were observed during inspection. This is not Wan generation readiness or operational cutover completion.

Isolated tests: `python -m unittest discover -s tests -v` from this module. They require the appropriate legacy optional dependencies; no production queue or generation request is used.
