# Single-target workflow deletion staging

The two existing planner task types and their IDs remain unchanged. The file
worker normalizes either to one execution target, then maps its machine ID to
one root-relative folder. No title, paths from payload, free text, arrays of
physical paths, or secondary mutations are sent. Only existing folder claims
are supported here; this does not authorize an arbitrary file delete.

| Item | Exact single folder | Current legacy coupling |
|---|---|---|
| Master Concept category | `binari/concept/sections/<categoryId>` | Per-file unlink; project concept index (section order, groups, category/item archive, Markdown index/digests/revision); contiguous priorities rewritten in unrelated legacy category files. |
| NPC | `binari/npcs/<npcId>` | Per-file unlink/empty-dir cleanup; global NPC catalog order, generation epoch, revision/hash; per-NPC generation retirement and guarded late-writer rejection. |

The opt-in flag `--single-target` prefixes existing CLI options. The fixed
loopback endpoint is `/v1/storage/delete-target`. For each existing row, readiness
must explicitly advertise its root. The exact POST uses one
`target:{root,path,kind:"directory"}` and the existing domain-specific claim.
The worker requires the ACK to match the task ID, confirmed target absence,
and a lowercase 64-digit recovery ID. There is no fallback to legacy when
selected. The worker does not follow the recovery ID or issue more writes.

The existing concept/NPC payload files, including owned NPC manifests/media,
already live under those exact roots. Shared data, backups, external history,
and neighboring folders are not deletion targets. A writer can quarantine the
exact source with one same-volume, no-replace folder move, verifying the
restorable source inventory and durable replay. Each namespace keeps its own
read/write lock, path/scope validation and canonical fencing. Its journal and
receipt are storage mechanics, not directives to delete additional user files.
Do not follow symlinks or accept a second mutation after an uncertain result.

## Catalog readiness (not implemented by this worker)

- Master Concept: treat group/archive/order as domain data and priorities as a
  derived, contiguous projection; no physical sibling writes. Suppress a
  catalog entry only for an exact committed quarantine, never silently accept
  arbitrary unreadable/missing data as deletion. Preserve selected folder's
  original placement on protected replacement/restore. The current replacement
  reads the exact task-bound selected source/title/placement backup, so persist
  a compatible independently verifiable proof before ACK (not another delete). Existing marker-based
  readers, raw folder downloaders and local NGS index consumers currently rely
  on the root index and cannot get a false dangling digest entry. To avoid
  mixed representation, either persist a separate domain catalog reconciliation
  step before the domain declares visibility/current-source readiness, or
  make the reader expose an equivalent reconciled index and update all raw
  clients/writers together. Do not enable the new route until one is proven.
- NPC: physical absence alone is not a generation retirement. The existing
  common file writer must preserve generation/lease history, fence old commits
  under its write lock, and reconcile the global catalog revision/document hash.
  The next preset create must see both the exact old generation retired and
  verified absence. Do not use direct runtime edits or a shared-data sweep.
- Both namespaces: crash before move = source still readable; crash after move
  but before ACK = exact quarantine replay, never repeat irreversible action
  on a newer incarnation. Cross-device move, unknown index or ambiguous
  absence, unavailable route, and authorization mismatch fail closed. Existing
  two archived remove jobs can use their existing claims unchanged after the
  native path, reader, recoverability and replacement projection are proven.

### BGM result folders

On the audited `audio.delete` Planner lane, the same resident recognizes
exactly one canonical directory from its run payload and frozen task input.
It first requires `canonical-files/binari/audio` in the generic PDS capability.
The worker sends the original audio request ID as `claim.sourceRequestId`
and the one matching root-relative directory target. There is no legacy
fallback. The original generation row is not updated.

Completion requires generic exact operation/absence/recovery acknowledgment.
The completed delete task uses no downstream result reference. Exact
pre-write rejection is eligible for the existing 10-minute/three-retry
policy; outcome uncertainty remains running for reconciliation. No new
worker service, launch arguments, or storage mount is needed on the worker.
