# NGS Workers

Unified worker source for NGS. This repository contains AudioForge (music generation/review/catalog), shared audio adapters, setup scripts, tests and official model manifests. Workers are modules in this single repository, not separate repositories.

## Quick start

1. Clone `https://github.com/pvenus/ngs-workers.git`.
2. Install Python 3.12 and read [team setup](docs/TEAM-SETUP.md).
3. Run `python3.12 scripts/init-workspace.py --root /absolute/new/workspace` to inspect the layout; add `--apply` for a new empty workspace.
4. Install AudioForge with `sh scripts/setup-audioforge.sh`; see [AudioForge](modules/audioforge/README.md).
5. Review [model setup](docs/MODEL-SETUP.md) before explicit downloads. The preferred music bundle is XL-SFT 4B + LM4B. Model weights are downloaded separately, not committed.
6. Configure private paths outside this checkout.

## Publication scope

Source repository, not a copy of operational state. No generated images/audio/video, databases, credentials, models, caches or operational binding files are included. Legacy LTX VideoForge source is retained for compatibility and provenance, without activation. Resident animation integration is a development candidate, not an enabled production service. Setup does not automatically enable production consumers or provide existing production data. The HQ adapter exists; runtime provisioning and deployment remain separate steps.

Upstream dependencies and downloaded models retain their own licenses. See the manifest and model setup for provenance. No new license grant is implied for project-owned code. Review-model and legacy-model restrictions are documented separately.

## Resident worker development (not activated)

The new [resident worker contracts and runtime](docs/RESIDENT-WORKERS.md) develop
five independently pulling roles in this same repository: document/Codex, image,
animation, image postprocessing, and music. This is an isolated development
candidate, not an operational migration. Formal Request/PDS adapters, independent
role composition, model-memory admission and automatic reconciliation are covered
by isolated contract tests. See [deployment prerequisites](docs/RESIDENT-DEPLOYMENT-CANDIDATE.md):
example configurations remain inactive until real service bindings and trusted
process proofs are supplied. Existing operating services and queues are unchanged.

## Removed standalone image editor

The standalone ImageForge implementation and installer are removed. Resident image generation and frame postprocessing remain independent and are preserved. Historical source remains recoverable from Git history.
