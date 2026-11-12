# Unified Workers (private)

The existing `workers/audio` package contains a music-only execution module for the existing NGS three-candidate workflow. NGS retains request authorization, candidate count, consecutive seeds, durable intent/unknown handling, selection and registration. The module delegates the existing ACE Turbo runner without changing its arguments or returned provenance. It does not implement LM4B/XL routing, SFX serialization, or new queues/services.

Deployment copies the four package leaves into a separately pinned release. An owner-private binding selects exact code/runtime/interpreter hashes and existing lock identities. No operational bindings, models, completed outputs or historical source trees belong in this repository. Importing the module loads no models. Missing or stale pins fail closed; both existing lock descriptors are inherited by the child.

The venv launcher path, resolved interpreter and pyvenv.cfg remain pinned. The initial music-boundary package did not itself migrate runtime or data. The subsequent operational handover placed AudioForge data under `Workers/data/audioforge`, the existing ACE runtime under `Workers/runtime/acestep`, and NGS audio artifacts under `Workers/data/ngs-audio` within the owner's main-SSD workspace. The music legacy lock is internal with a compatibility alias at its old name; its original file is preserved, and the shared generation lock remains a distinct role. These completed moves do not provision a new teammate's model/runtime or grant access to existing data. SFX producers and reciprocal crash behavior of other Audio services remain outside this music-only boundary. The inherited backend final wait after SIGKILL remains unbounded.

Private validation included 22 isolated subprocess/loader tests and 7 existing NGS approval/three-candidate tests. No new real inference was performed after connection. Full integration fixtures and deployment evidence are retained privately outside this minimal source repository. This is not public-release or license clearance, and no remote publication is authorized.

## Unified worker source registration

`modules/audioforge` now contains the existing operational AudioForge package and isolated tests, not just the original music boundary. New-machine code installation: `sh scripts/setup-audioforge.sh`. See [worker setup and limits](modules/audioforge/README.md) and the [private team setup checklist](docs/TEAM-SETUP.md). This source registration does not change operating launchd services or move data. Video/character workers and model-runtime installation are still separate migration work; this repository is not yet a complete public bootstrap.

## Git-backed AudioForge operation

The four existing AudioForge roles have a single explicit launcher: `scripts/run-audioforge.py`. It selects this Git package for the service and its review subprocesses, requires data outside the source tree, and never silently starts a mock generation backend. `--check` performs no DB/model/worker startup. The service venv can live separately through `WORKERS_AUDIO_VENV`; model environments remain separate. See the AudioForge module README for setup and foreground commands.

`modules/videoforge` preserves legacy LTX-only source for provenance. It is **not** activated by this launcher or the AudioForge setup, and is not a Wan implementation. LTX migration and new video features remain on hold. No public distribution/license clearance is implied.

## Git-backed ImageForge operation

The current ImageForge HTTP package, service environment, shared resources and preserved legacy reads operate internally. Select the documented `IMAGEFORGE_STORAGE_MODE=internal` contract explicitly; omitting it retains the legacy external-storage mode, not a portable default. See [ImageForge setup](modules/imageforge/README.md) and [team prerequisites](docs/TEAM-SETUP.md). Existing external originals are rollback/archive material, not a source to overwrite newer internal state. The current health endpoint is `/api/health` on port 8000. Health checks are not end-to-end content acceptance tests.
