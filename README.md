# Unified Workers (private)

The existing `workers/audio` package contains a music-only execution module for the existing NGS three-candidate workflow. NGS retains request authorization, candidate count, consecutive seeds, durable intent/unknown handling, selection and registration. The module delegates the existing ACE Turbo runner without changing its arguments or returned provenance. It does not implement LM4B/XL routing, SFX serialization, or new queues/services.

Deployment copies the four package leaves into a separately pinned release. An owner-private binding selects exact code/runtime/interpreter hashes and existing lock identities. No operational bindings, models, completed outputs or historical source trees belong in this repository. Importing the module loads no models. Missing or stale pins fail closed; both existing lock descriptors are inherited by the child.

The venv launcher path is preserved, while its resolved interpreter and pyvenv.cfg are pinned. Runtime/model migration is not completed by this package: the selected model runtime, venv and weights reuse the existing internal AudioWorker installation; the byte-identical runner is included in the pinned Workers release. The original external legacy lock and existing NGS audio artifact storage remain deliberately unchanged. SFX producers and reciprocal crash behavior of other Audio services are outside this music-only change. The inherited backend final wait after SIGKILL remains unbounded.

Private validation included 22 isolated subprocess/loader tests and 7 existing NGS approval/three-candidate tests. No new real inference was performed after connection. Full integration fixtures and deployment evidence are retained privately outside this minimal source repository. This is not public-release or license clearance, and no remote publication is authorized.

## Unified worker source registration

`modules/audioforge` now contains the existing operational AudioForge package and isolated tests, not just the original music boundary. New-machine code installation: `sh scripts/setup-audioforge.sh`. See [worker setup and limits](modules/audioforge/README.md) and the [private team setup checklist](docs/TEAM-SETUP.md). This source registration does not change operating launchd services or move data. Video/character workers and model-runtime installation are still separate migration work; this repository is not yet a complete public bootstrap.

## Git-backed AudioForge operation

The four existing AudioForge roles have a single explicit launcher: `scripts/run-audioforge.py`. It selects this Git package for the service and its review subprocesses, requires data outside the source tree, and never silently starts a mock generation backend. `--check` performs no DB/model/worker startup. The service venv can live separately through `WORKERS_AUDIO_VENV`; model environments remain separate. See the AudioForge module README for setup and foreground commands.

`modules/videoforge` preserves legacy LTX-only source for provenance. It is **not** activated by this launcher or the AudioForge setup, and is not a Wan implementation. LTX migration and new video features remain on hold. No public distribution/license clearance is implied.
