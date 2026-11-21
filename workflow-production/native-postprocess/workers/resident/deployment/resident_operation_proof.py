"""Operation-scoped native proof primitives. No admission or global installation.

Domain readers are trusted service callbacks, never request-provided assertions.
They must validate original DTO/reference/descendant completeness. This module
independently checks physical ownership, issued attempts, source pins and stops.
"""
from dataclasses import dataclass, asdict
import hashlib
import inspect
import json
from pathlib import Path
from ..contracts import Claim, PROFILES
from ..adapters.attempt_directory_lock import directory_lock,directory_read_cohort
from .request_selector import digest
from .resident_frame_proof import checked


@dataclass(frozen=True)
class OperationScope:
    guard: dict
    request_ids: tuple
    binding_hash: str


def require(condition, code):
    if not condition:
        raise ValueError('RESIDENT_OPERATION_' + code)


class ResidentOperationAllocations:
    def __init__(self, authority, scope_reader, *, role, producer, kind,
                 scratch_root, root_key, managed_root, worker_id, legacy=None):
        require(role in ('document', 'image', 'animation') and producer in
                ('planning', 'portrait', 'pose', 'animation'), 'ROLE')
        expected = {'planning': ('document', 'planning-attempt'),
                    'portrait': ('image', 'portrait-stage'),
                    'pose': ('image', 'pose-request'),
                    'animation': ('animation', 'animation-job')}
        require(expected[producer] == (role, kind), 'KIND')
        self.authority, self.common = authority, authority.common
        self.scope_reader, self.legacy = scope_reader, legacy
        self.role, self.producer, self.kind = role, producer, kind
        self.scratch, self.managed = checked(scratch_root), checked(managed_root)
        require(self.managed in self.scratch.parents and self.scratch.is_dir()
                and worker_id and root_key and callable(scope_reader), 'ROOT')
        self.root_key, self.worker_id = root_key, worker_id

    def scope(self, g):
        s = self.scope_reader(dict(g))
        require(isinstance(s, OperationScope) and s.guard == g, 'GUARD_CHANGED')
        require(isinstance(s.binding_hash, str) and len(s.binding_hash) == 64
                and all(c in '0123456789abcdef' for c in s.binding_hash), 'SCOPE_HASH')
        require(isinstance(s.request_ids, tuple) and
                len(s.request_ids) == len(set(s.request_ids)), 'SCOPE_IDS')
        # Completeness of domain descendants comes from the pinned domain reader;
        # each selected row must independently reach the original physical owner.
        for rid in s.request_ids:
            require(isinstance(rid, str) and rid, 'REQUEST_ID')
            seen = set()
            cursor = rid
            while cursor != g['requestId']:
                require(cursor and cursor not in seen, 'DESCENDANT')
                seen.add(cursor)
                cursor = self.common.row(cursor)['parent_id']
        return s

    def claims(self, g):
        scope = self.scope(g)
        result = []
        db = self.common.db
        for rid in scope.request_ids:
            row = self.common.row(rid)
            require(not db.execute("SELECT 1 FROM common_events WHERE request_id=? AND kind='legacy_import'", (rid,)).fetchone(), 'LEGACY_EXECUTION')
            records = db.execute('SELECT * FROM resident_resource_grants WHERE request_id=? ORDER BY rowid', (rid,)).fetchall()
            issued = {(int(r['attempt_id']), r['worker_id']) for r in records}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='resident_reconciliations'").fetchone():
                for recovery in db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id IN (SELECT grant_id FROM resident_resource_grants WHERE request_id=?)', (rid,)):
                    if recovery[0]:
                        value = json.loads(recovery[0])
                        issued.add((value['fence'], value['workerId']))
            require(not row['fence'] or any(f == row['fence'] for f, _ in issued), 'UNTRACKED_FENCE')
            for event in db.execute("SELECT details_json FROM common_events WHERE request_id=? AND kind IN ('claimed','lease_recovered')", (rid,)):
                detail = json.loads(event[0])
                require((detail.get('fence'), detail.get('worker')) in issued, 'UNTRACKED_EXECUTION')
            for record in records:
                value = json.loads(record['claim_json'])
                value['input_refs'] = tuple(value['input_refs'])
                c = Claim(**value)
                c.validate(PROFILES[self.role])
                require(c.request_id == rid and c.resource_grant == record['grant_id']
                        and c.attempt_id == record['attempt_id']
                        and record['worker_id'] == self.worker_id
                        and record['resource'] == PROFILES[self.role].resource, 'CLAIM_CHANGED')
                pin = db.execute('SELECT binding_json FROM resident_claim_bindings WHERE grant_id=?', (c.resource_grant,)).fetchone()
                require(pin is not None, 'BINDING_MISSING')
                binding = json.loads(pin[0])
                require(binding.get('targetId') == g['npcId']
                        and binding.get('projectId') == row['project_id']
                        and binding.get('expectedRevision') == c.expected_revision, 'TARGET_CHANGED')
                path = checked(self.scratch / digest([rid, c.attempt_id]))
                with directory_lock(path) as exists:
                    if exists:
                        claim_path = checked(path / 'claim.json')
                        require(path.is_dir() and claim_path.is_file() and
                                json.loads(claim_path.read_text()) == json.loads(json.dumps(asdict(c))), 'WORKSPACE_OWNER')
                result.append((c, path, record))
        return result

    def entries(self, g):
        values = list(self.legacy.entries(g)) if self.legacy else []
        # Legacy paths may be retained as claims, never silently assumed stopped.
        require(not any(checked(v['path']).exists() for v in values), 'LEGACY_WORKSPACE')
        for c, path, _ in self.claims(g):
            values.append({k: g[k] for k in ('npcId', 'generationId', 'requestId')} |
                          {'allocated': True, 'exclusive': True, 'rootKey': self.root_key,
                           'kind': self.kind, 'path': str(path),
                           'authorityType': 'resident-issued-claim',
                           'authorityHash': digest(asdict(c))})
        require(len({(v['kind'], v['path']) for v in values}) == len(values), 'DUPLICATE_PATH')
        return values

    def __call__(self, g, kind, path):
        found = [v for v in self.entries(g) if (v['kind'], v['path']) == (kind, path)]
        require(len(found) == 1, 'EXACT_ALLOCATION')
        return found[0]

    def verify_finish(self, g):
        scope, claims = self.scope(g), self.claims(g)
        require(scope.request_ids and claims, 'NO_ISSUED_EXECUTION')
        for c, _, _ in claims:
            require(self.authority.verify_stopped(c) is True, 'EXECUTION_UNCONFIRMED')
        for rid in scope.request_ids:
            row = self.common.row(rid)
            require(row['state'] == 'completed' and row['worker'] is None, 'NOT_COMPLETED')
            current = [c for c, _, _ in claims if c.request_id == rid and c.attempt_id == str(row['fence'])]
            if not current and self.common.db.execute("SELECT 1 FROM sqlite_master WHERE name='resident_reconciliations'").fetchone():
                for candidate, _, _ in claims:
                    if candidate.request_id != rid:
                        continue
                    recovery = self.common.db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id=?', (candidate.resource_grant,)).fetchone()
                    if recovery and recovery[0] and json.loads(recovery[0]).get('fence') == row['fence']:
                        current.append(candidate)
            require(len(current) == 1, 'FINAL_ATTEMPT')
            c = current[0]
            checkpoint = self.common.db.execute('SELECT receipt_json,proof_json FROM resident_publication_checkpoints WHERE grant_id=?', (c.resource_grant,)).fetchone()
            require(checkpoint is not None, 'CHECKPOINT_REQUIRED')
            receipt, proof = json.loads(checkpoint[0]), json.loads(checkpoint[1])
            require(all(receipt.get(k) == v for k, v in
                        {'request_id': rid, 'attempt_id': c.attempt_id, 'fencing_token': c.fencing_token}.items())
                    and proof.get('sourceReceipt', {}).get('requestId') == rid
                    and proof.get('publication', {}).get('verified') is True, 'CHECKPOINT_CHANGED')
        # Original factory receipts must still independently read real PDS state.
        return True


class ResidentOperationRoutingProbe:
    def __init__(self, allocations, *, source_root, source_pins, worker_config,
                 workspace_for_claim, backend_for_claim, workspace_types,
                 backend_types, route_claims, host_id, registry):
        self.allocations = allocations
        self.source, self.pins = checked(source_root), dict(source_pins)
        self.config, self.workspace, self.backend = worker_config, workspace_for_claim, backend_for_claim
        self.workspace_types, self.backend_types = tuple(workspace_types), tuple(backend_types)
        self.route_claims, self.host, self.registry = route_claims, host_id, registry
        require(registry.get('producers') == [allocations.producer] and
                host_id in registry.get('hosts', []), 'SCOPED_REGISTRY')
        require(self.workspace_types and self.backend_types and all(callable(x) for x in
                (workspace_for_claim, backend_for_claim, route_claims)), 'ROUTE_READERS')
        required = {'workers/resident/runtime.py', 'workers/resident/deployment/role_factory.py',
                    'workers/resident/deployment/resident_operation_proof.py',
                    'workers/resident/adapters/attempt_directory_lock.py'}
        for cls in self.workspace_types + self.backend_types:
            path = checked(inspect.getfile(cls))
            require(self.source in path.parents, 'TYPE_SOURCE')
            required.add(str(path.relative_to(self.source)))
        require(required <= self.pins.keys(), 'SOURCE_COVERAGE')

    def __call__(self, producer, g):
        from .composition import verify
        require(producer == self.allocations.producer, 'UNRELATED_PRODUCER')
        verify(self.config, self.allocations.role)
        require(self.config.get('activationApproved') is True and
                self.config['workerId'] == self.allocations.worker_id, 'ACTIVE_CONFIG')
        settings = self.config.get('settings', {})
        require(settings.get('workerId') == self.allocations.worker_id and
                checked(settings.get('poseScratchRoot' if self.allocations.producer == 'pose' else 'scratchRoot', '')) == self.allocations.scratch, 'ACTIVE_ROOT')
        for relative, sha in self.pins.items():
            path = checked(self.source / relative)
            require(self.source in path.parents and path.is_file() and
                    hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'SOURCE_CHANGED')
        scope = self.allocations.scope(g)
        # Route hints are independently derived from original domain rows. They
        # must cover every selected request, including not-yet-issued requests.
        claims = list(self.route_claims(g))
        expected_ids = set(scope.request_ids) or {g['requestId']}
        require({c.request_id for c in claims} == expected_ids and
                len(claims) == len(expected_ids), 'ROUTE_SCOPE')
        observed = []
        for c in claims:
            require(c.capability in PROFILES[self.allocations.role].capabilities, 'ROUTE_CAPABILITY')
            workspace, backend = self.workspace(c), self.backend(c)
            require(type(workspace) in self.workspace_types and type(backend) in self.backend_types
                    and checked(workspace.root) == self.allocations.scratch, 'INSTANTIATED_ROUTE')
            observed.append({'requestId': c.request_id,
                             'workspace': type(workspace).__module__ + '.' + type(workspace).__name__,
                             'backend': type(backend).__module__ + '.' + type(backend).__name__,
                             'root': str(workspace.root)})
        require(observed, 'NO_ROUTE')
        return {'verified': True, 'hostId': self.host, 'producer': producer,
                'producerRegistryHash': digest(self.registry), 'allOutputHooks': True,
                'rootKeys': [self.allocations.root_key], 'sourceManifestHash': digest(self.pins),
                'actualRoutesHash': digest(observed), 'metadataOnly': False}


def bind_operation_factory(factory, allocations):
    """Wrap a *new request-local* native factory. No process-global mutation.

    Caller installs allocations and routing probe in its normal HostReports /
    bootstrap. Original coordinator, guard, baseline and terminal checks remain.
    """
    require(factory.registry['producers'] == [allocations.producer], 'FACTORY_SCOPE')
    require(not getattr(factory, '_resident_operation_bound', False), 'FACTORY_ALREADY_BOUND')
    original = factory.receipts
    def receipts(g):
        allocations.verify_finish(g)
        return original(g)
    factory.receipts = receipts
    factory._resident_operation_bound = True
    # Preserve the real RegistryFactory contract without changing its class or
    # any global factory. Only this request-local producer's inventory is wrapped.
    from local.services.npc_scratch_registry import RegistryFactory
    require(isinstance(factory,RegistryFactory),'FACTORY_TYPE')
    class CohortFactory(RegistryFactory):
        def __call__(self,guard):
            output=super().__call__(guard);inventory=output._inventory
            def locked_inventory():
                # Only resident attempt directories participate. Original legacy
                # metadata files retain their native inventory contract.
                paths=[value[2] for value in output.workspaces.values()
                       if value[2].parent == allocations.scratch]
                with directory_read_cohort(paths):return inventory()
            output._inventory=locked_inventory
            return output
    bound=CohortFactory.__new__(CohortFactory);bound.__dict__.update(factory.__dict__)
    return bound


class OperationFactoryRouter:
    """One host router, immutable request selection. Selector reads authority DB.

    Builders return isolated factories (including an existing frames builder).
    Do not implement builders by mutating process-global native configuration.
    """
    def __init__(self, selector, builders):
        import threading
        require(callable(selector) and builders and all(callable(v) for v in builders.values()), 'ROUTER')
        self.selector, self.builders = selector, dict(builders)
        self._selected, self._lock = {}, threading.RLock()

    def __call__(self, guard):
        key = tuple(guard.get(k) for k in ('npcId', 'generationId', 'requestId'))
        require(all(key) and guard.get('leaseToken'), 'ROUTER_GUARD')
        with self._lock:
            operation = self.selector(dict(guard))
            require(operation in self.builders, 'ROUTER_OPERATION')
            old = self._selected.get(key)
            if old is not None:
                require(old[0] == operation and old[1] == guard, 'ROUTER_SCOPE_CHANGED')
            else:
                self._selected[key] = (operation, dict(guard))
        # Build in the current authority scope; never cache a thread SQLite handle.
        factory = self.builders[operation](dict(guard))
        return factory(dict(guard))
