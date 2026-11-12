# ImageForge worker

Unified Workers module. HTTP image operations use Pillow/NumPy, not a downloaded generation model. Install this package for the HTTP worker; the MCP CLI separately requires the `mcp` extra. Browser UI and server are included in the installed wheel. Private legacy inventory and files are deliberately not in Git.

Existing shared storage contract is unchanged. Set `IMAGEFORGE_LEGACY_ROOT` and `IMAGEFORGE_LEGACY_INVENTORY` to the preserved original installation for legacy read compatibility. Do not copy the queue or alter the shared-root security contract for a code-only move.

## Reproduce HTTP installation

From the unified Workers checkout:

```sh
python3.12 -m venv .venvs/imageforge
.venvs/imageforge/bin/python -m pip install -r modules/imageforge/requirements.http.lock.txt
.venvs/imageforge/bin/python -m pip install --no-deps modules/imageforge
# Set preserved storage bindings before operating against existing files.
IMAGEFORGE_HOST=127.0.0.1 IMAGEFORGE_PORT=8000 .venvs/imageforge/bin/python -m imageforge_mcp.cli
```

Do not run this command alongside the operating service on the same port. The macOS operational launcher retains the existing external-volume identity check. Source and Python environment now run on the internal SSD; shared resources and private legacy inventory remain on the external disk. This is not full data migration or external-disk independence. The HTTP server holds browser edit sessions in memory, so drain active editing before restart.

Verification: fresh internal venv wheel install; `/api/health`, `/`, `/web/index.html`, `/openapi.json` returned 200 from an alternate-port installed package; six image-operation tests passed. Existing port 8000 then verified after launchd switch. No new generation, model download, production upload or extensive content test was performed. Existing upstream formal request routing and resource publisher checks are unchanged.

## Internal storage (current operational mode)

Set `IMAGEFORGE_STORAGE_MODE=internal`, `IMAGEFORGE_STORAGE_BASE=$HOME/Workspaces/Workers/data/imageforge`, `IMAGEFORGE_LEGACY_ROOT=$IMAGEFORGE_STORAGE_BASE/legacy`, and `IMAGEFORGE_LEGACY_INVENTORY=$IMAGEFORGE_LEGACY_ROOT/legacy_inventory.json`. Provision the base as owner-only, `resources` and `resources/v1` as 0755, and `resources/v1/imageforge` as 0700. Canonical paths, owner/mode/ACL, symlink rejection and same-device boundaries are checked before publishing. The operational launcher now checks this internal storage contract, not an external SSD UUID.

The previous section's external-storage state describes the earlier code-only migration. Current operating source/runtime/shared resource root/legacy reads are internal. External originals are preserved as rollback/archive only. The old external shared ImageForge namespace did not exist, so only 305 legacy files needed copying; new shared jobs use the provisioned internal namespace.
