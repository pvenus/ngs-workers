# NGS Workers

Unified worker source for NGS. This repository contains AudioForge (music generation/review/catalog), ImageForge (image processing), shared audio adapters, setup scripts, tests and official model manifests. Workers are modules in this single repository, not separate repositories.

## Quick start

1. Clone `https://github.com/pvenus/ngs-workers.git`.
2. Install Python 3.12 and read [team setup](docs/TEAM-SETUP.md).
3. Run `python3.12 scripts/init-workspace.py --root /absolute/new/workspace` to inspect the layout; add `--apply` for a new empty workspace.
4. Install AudioForge with `sh scripts/setup-audioforge.sh`; see [AudioForge](modules/audioforge/README.md) and [ImageForge](modules/imageforge/README.md).
5. Review [model setup](docs/MODEL-SETUP.md) before explicit downloads. The preferred music bundle is XL-SFT 4B + LM4B. Model weights are downloaded separately, not committed.
6. Configure private paths outside this checkout. For ImageForge use `IMAGEFORGE_STORAGE_MODE=internal` explicitly.

## Publication scope

Initial source snapshot, not a copy of operational state or private Git history. No generated images/audio/video, databases, credentials, models, caches or operational binding files are included. Legacy LTX VideoForge is excluded. Wan-Animate/video preset integration is not included in this initial snapshot. Setup does not automatically enable production consumers or provide existing production data. The HQ adapter exists; runtime provisioning and deployment remain separate steps.

Upstream dependencies and downloaded models retain their own licenses. See the manifest and model setup for provenance. No new license grant is implied for project-owned code. Review-model and legacy-model restrictions are documented separately.
