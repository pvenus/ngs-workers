"""Request-SERVICE-side bridge to existing NGS CommonRequests.

Never instantiate this in a worker process. The existing authority owns its DB.
The authenticated HTTP pull facade is implemented separately in request_api.
Schema setup and operating bindings remain explicit; import never migrates a DB.
"""
import json
from dataclasses import asdict
import hashlib
import secrets
from contextlib import contextmanager
from ..contracts import Claim, PROFILES

TYPE_CAPABILITIES = {
    'npc.animation.generate': 'animation.generate',
    'npc.animation.frames.generate': 'frames.extract',
}

EXACT_CAPABILITIES = {
    ('npc.pose.generate-direction','ngs.pose-direction-intake.v1','pose.direction.requests'): 'image.generate',
    ('npc.workflow','ngs.npc-category-candidate.v1','npc.category-candidate'): 'document.edit',
    ('npc.workflow.stage','npc-workflow-portrait-single.v1','npc.workflow.portrait'): 'image.generate',
    ('npc.workflow.stage','npc-workflow-portrait-single.v1','npc.single.portrait'): 'image.generate',
    ('audio.music.generate','audio-music-hq.v1','audio.music'): 'music.generate',
}


SCHEMA = '''CREATE TABLE IF NOT EXISTS resident_resource_grants(
 grant_id TEXT PRIMARY KEY,resource TEXT NOT NULL,request_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,lease_json TEXT NOT NULL,worker_id TEXT NOT NULL,
 claim_json TEXT NOT NULL,released INTEGER NOT NULL DEFAULT 0)'''


class CommonAuthorityBridge:
    def __init__(self, authority, transaction_lock, select_payload, verify_publication,
                 capacities, *, verify_stopped, lease_seconds=90, guard_resolver=None):
        self.common = authority
        self.lock = transaction_lock
        self.select_payload = select_payload
        self.verify_publication = verify_publication
        self.verify_stopped = verify_stopped
        self.failure_fence = None
        self.attempt_liveness = None
        self.timeout_disposition = None
        self.worker_managed_resources = frozenset()
        self.guard_resolver = guard_resolver
        self.capacities = dict(capacities)
        if any(type(v) is not int or v<1 for v in self.capacities.values()):
            raise ValueError('INVALID_RESOURCE_CAPACITY')
        if self.capacities.get('video-global') != 1:
            raise ValueError('VIDEO_GLOBAL_CAPACITY_MUST_BE_ONE')
        if not 5 <= lease_seconds <= 3600:
            raise ValueError('INVALID_LEASE_SECONDS')
        self.lease_seconds = lease_seconds

    def initialize_schema(self):
        # Not invoked by import, constructor or the worker CLI.
        with self.lock, self.common.tx():
            self.common.db.execute(SCHEMA)
            self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_publication_checkpoints(grant_id TEXT PRIMARY KEY,receipt_json TEXT NOT NULL,proof_json TEXT NOT NULL)')
            self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_claim_bindings(grant_id TEXT PRIMARY KEY,binding_json TEXT NOT NULL)')
            self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_publications(grant_id TEXT PRIMARY KEY, publication_hash TEXT NOT NULL, state TEXT NOT NULL, prepared_at REAL NOT NULL, receipt_json TEXT)')
            self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_claim_commands(worker_id TEXT,key TEXT,role TEXT,claim_json TEXT,PRIMARY KEY(worker_id,key))')

    @contextmanager
    def transaction(self):
        with self.lock, self.common.tx():
            yield

    def _capability(self,row,rid):
        # A shared request type (e.g. workflow.stage) cannot select a worker by type alone.
        refs=self.common.db.execute('SELECT domain,schema_version FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
        exact={EXACT_CAPABILITIES.get((row['request_type'],r['schema_version'],r['domain'])) for r in refs}
        exact.discard(None)
        if len(exact)>1:raise ValueError('AMBIGUOUS_FORMAL_REQUEST_ROUTE')
        return next(iter(exact)) if exact else TYPE_CAPABILITIES.get(row['request_type'])

    def claim(self, profile, worker_id):
        if PROFILES.get(profile.role) != profile:
            raise ValueError('NONCANONICAL_WORKER_PROFILE')
        with self.transaction():
            capacity = self.capacities.get(profile.resource, 0)
            count = self.common.db.execute(
                'SELECT count(*) FROM resident_resource_grants WHERE resource=? AND released=0',
                (profile.resource,)).fetchone()[0]
            if profile.resource not in self.worker_managed_resources and count >= capacity:
                return None
            item = self.select_payload(profile)
            if item is None:
                return None
            rid = item['requestId']
            from .host_boundary_recovery import assert_open
            assert_open(self.common,rid)
            from .logical_retry import request_open
            request_open(self.common,rid)
            row = self.common.row(rid)
            # Never reclaim an expired running lease based on time alone.
            if row['state'] not in ('queued', 'retry_wait') or row['retry_at']>self.common.clock():
                return None
            if (item['requestType'] != row['request_type']
                    or self._capability(row,rid) != item['capability']):
                raise ValueError('FORMAL_REQUEST_TYPE_MISMATCH')
            computed=hashlib.sha256(json.dumps(item['parameters'],ensure_ascii=False,sort_keys=True,
                                                separators=(',',':')).encode()).hexdigest()
            if computed != item['inputHash']:
                raise ValueError('FORMAL_INPUT_HASH_MISMATCH')
            if item['inputHash'] not in [r[0] for r in self.common.db.execute(
                    'SELECT input_hash FROM common_payload_refs WHERE request_id=?',(rid,))]:
                raise ValueError('FORMAL_INPUT_HASH_MISMATCH')
            if self.failure_fence is not None:self.failure_fence.assert_claim_allowed(rid)
            lease = self.common.claim(rid, worker_id, 'resident.claim.'+str(row['revision']),
                                      lease_seconds=self.lease_seconds, states=('queued','retry_wait'))
            grant = secrets.token_hex(16)
            claim = Claim(rid, str(lease['fence']), str(lease['fence']), item['capability'],
                          tuple(item['inputRefs']), item['parameters'], grant,
                          item['expectedRevision'])
            claim.validate(profile)
            binding=item.get('binding',{'projectId':row['project_id'],
                'targetId':claim.parameters.get('npcId',claim.parameters.get('targetId',claim.request_id)),
                'expectedRevision':claim.expected_revision})
            if (set(binding)!={'projectId','targetId','expectedRevision'} or binding['projectId']!=row['project_id']
                    or binding['expectedRevision']!=claim.expected_revision or not isinstance(binding['targetId'],str) or not binding['targetId']):
                raise ValueError('FORMAL_TARGET_BINDING_INVALID')
            self.common.db.execute('INSERT INTO resident_claim_bindings VALUES(?,?)',(grant,json.dumps(binding,sort_keys=True)))
            self.common.db.execute('INSERT INTO resident_resource_grants VALUES(?,?,?,?,?,?,?,0)',
                (grant,profile.resource,rid,claim.attempt_id,json.dumps(lease),worker_id,json.dumps(asdict(claim),sort_keys=True)))
            if self.failure_fence is not None:self.failure_fence.track_claim(claim)
            return claim

    def claim_idempotent(self, profile, worker_id, key):
        with self.transaction():
            row=self.common.db.execute('SELECT role,claim_json FROM resident_claim_commands WHERE worker_id=? AND key=?',(worker_id,key)).fetchone()
            if row:
                if row['role']!=profile.role:raise ValueError('CLAIM_KEY_CONFLICT')
                if row['claim_json'] is None:return None
                raw=json.loads(row['claim_json']);raw['input_refs']=tuple(raw['input_refs'])
                c=Claim(**raw)
                if self.attempt_liveness is not None:
                    from .attempt_liveness import timeout,assert_live
                    if timeout(self.common,c)is not None:return None
                    assert_live(self.common,c)
                ack=getattr(self,'host_recovery_ack',None)
                if ack is not None and ack(c):return None
                from .host_boundary_recovery import assert_open
                assert_open(self.common,c.request_id)
                grant=self.common.db.execute('SELECT released FROM resident_resource_grants WHERE grant_id=?',(c.resource_grant,)).fetchone()
                return None if grant and grant['released'] else c
            c=self.claim(profile,worker_id)
            self.common.db.execute('INSERT INTO resident_claim_commands VALUES(?,?,?,?)',
                (worker_id,key,profile.role,json.dumps(asdict(c),sort_keys=True) if c else None))
            return c

    def _lease(self, claim, allow_released=False):
        from .host_boundary_recovery import assert_open
        assert_open(self.common,claim.request_id)
        if self.attempt_liveness is not None:
            from .attempt_liveness import assert_live
            assert_live(self.common,claim)
        return self._issued_lease(claim,allow_released)

    def _issued_lease(self,claim,allow_released=False):
        # Exact immutable issued identity only; conveys NO live business authority.
        from .host_boundary_recovery import assert_open
        assert_open(self.common,claim.request_id)
        row = self.common.db.execute('SELECT * FROM resident_resource_grants WHERE grant_id=?',
                                     (claim.resource_grant,)).fetchone()
        if row is None or row['request_id'] != claim.request_id or row['attempt_id'] != claim.attempt_id:
            raise ValueError('RESOURCE_GRANT_MISMATCH')
        if row['claim_json']!=json.dumps(asdict(claim),sort_keys=True):
            raise ValueError('ISSUED_CLAIM_CHANGED')
        if row['released'] and not allow_released:
            raise ValueError('RESOURCE_ALREADY_RELEASED')
        lease = json.loads(row['lease_json'])
        if str(lease['fence']) != claim.fencing_token:
            raise ValueError('RESOURCE_FENCE_MISMATCH')
        return lease

    def mark_publication_started(self,claim):
        if self.failure_fence is None:raise ValueError('PUBLICATION_TRACKING_NOT_CONFIGURED')
        with self.transaction():
            self.authorize_publication(claim)
            return self.failure_fence.mark_publication_started(claim)

    def prepare_publication(self,claim,publication_hash):
        import re
        if not isinstance(publication_hash,str) or not re.fullmatch('[a-f0-9]{64}',publication_hash):raise ValueError('PUBLICATION_HASH_INVALID')
        with self.transaction():
            if self.failure_fence is not None:self.failure_fence.assert_publication_allowed(claim)
            self._lease(claim)
            row=self.common.db.execute('SELECT * FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if row:
                if row['publication_hash']!=publication_hash:raise ValueError('PUBLICATION_HASH_CONFLICT')
            else:
                self.authorize_publication(claim)
                self.common.db.execute('INSERT INTO resident_publications VALUES(?,?,?,?,NULL)',(claim.resource_grant,publication_hash,'prepared',self.common.clock()))
            binding=json.loads(self.common.db.execute('SELECT binding_json FROM resident_claim_bindings WHERE grant_id=?',(claim.resource_grant,)).fetchone()[0])
            return {'reserved':True,'requestId':claim.request_id,'attemptId':claim.attempt_id,
                    'fencingToken':claim.fencing_token,'publicationHash':publication_hash,
                    'reservationId':claim.resource_grant,'state':row['state'] if row else 'prepared',
                    'capability':claim.capability,'expectedRevision':claim.expected_revision,
                    'requestHash':hashlib.sha256(json.dumps(claim.parameters,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                    'projectId':binding['projectId'],'targetId':binding['targetId']}

    def finish_publication(self,claim,publication_hash,receipt):
        with self.transaction():
            self._lease(claim)
            row=self.common.db.execute('SELECT * FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if not row or row['publication_hash']!=publication_hash:raise ValueError('PUBLICATION_RESERVATION_REQUIRED')
            scope=self.prepare_publication(claim,publication_hash)
            fields=('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')
            if (not isinstance(receipt,dict) or receipt.get('verified') is not True
                    or receipt.get('publicationHash')!=publication_hash or receipt.get('authority')!='filex'
                    or receipt.get('status')!='committed' or any(receipt.get(k)!=scope[k] for k in fields)):
                raise ValueError('PUBLICATION_COMMIT_RECEIPT_REQUIRED')
            calculated=hashlib.sha256(json.dumps({k:v for k,v in receipt.items() if k!='receiptHash'},
                sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
            if receipt.get('receiptHash')!=calculated:raise ValueError('PUBLICATION_RECEIPT_HASH_INVALID')
            raw=json.dumps(receipt,sort_keys=True,separators=(',',':'))
            if row['receipt_json'] is not None and row['receipt_json']!=raw:raise ValueError('PUBLICATION_COMMIT_CHANGED')
            self.common.db.execute("UPDATE resident_publications SET state='committed',receipt_json=? WHERE grant_id=?",(raw,claim.resource_grant))
            return {'committed':True,'requestId':claim.request_id,'publicationHash':publication_hash}

    def _publication_pending(self,claim):
        row=self.common.db.execute('SELECT state FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
        if row and row['state']=='prepared':raise ValueError('PUBLICATION_RESERVATION_UNRESOLVED')

    def ownership_guard(self,claim):
        if not callable(self.guard_resolver):raise ValueError('PDS_GUARD_RESOLVER_NOT_CONFIGURED')
        def snapshot():
            lease=self._lease(claim);self.common.owned(claim.request_id,lease)
            row=self.common.row(claim.request_id)
            if row['state'] not in ('processing','synchronizing'):raise ValueError('PDS_GUARD_REQUEST_NOT_ACTIVE')
            binding=self.common.db.execute('SELECT binding_json FROM resident_claim_bindings WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if binding is None:raise ValueError('PDS_GUARD_BINDING_REQUIRED')
            return lease,binding[0]
        with self.transaction():
            original_lease,original_binding=snapshot()
        # Original service constructors may initialize schemas with executescript;
        # PDS/native admission can also block. Neither belongs inside Request tx.
        result=self.guard_resolver(claim)
        with self.transaction():
            lease,raw=snapshot()
            if lease!=original_lease or raw!=original_binding:raise ValueError('PDS_GUARD_AUTHORITY_CHANGED')
            binding=json.loads(raw)
            guard=result.get('guard') if isinstance(result,dict) else None
            if (not isinstance(guard,dict) or set(guard)!={'npcId','generationId','requestId','leaseToken'}
                    or any(not isinstance(v,str) or not v for v in guard.values())
                    or guard['npcId']!=binding['targetId'] or result.get('stageRequestId')!=claim.request_id
                    or result.get('scopeRequestId')!=guard['requestId']):raise ValueError('PDS_GUARD_SCOPE_MISMATCH')
            return dict(guard)

    def authorize_publication(self, claim):
        # Service-to-service read, never accepts a worker's assertion of ownership.
        with self.transaction():
            if self.failure_fence is not None:self.failure_fence.assert_publication_allowed(claim)
            lease=self._lease(claim)
            self.common.owned(claim.request_id,lease)
            row=self.common.row(claim.request_id)
            if row['state'] not in ('processing','synchronizing'):
                raise ValueError('PUBLICATION_REQUEST_NOT_ACTIVE')
            return {'authorized':True,'requestId':claim.request_id,'attemptId':claim.attempt_id,
                    'fencingToken':claim.fencing_token,'leaseUntil':row['lease_until'],
                    'capability':claim.capability,'expectedRevision':claim.expected_revision}

    def renew(self, claim):
        with self.transaction():
            try:
                lease = self._lease(claim)
                self.common.heartbeat(claim.request_id, lease, seconds=self.lease_seconds)
                return True
            except ValueError:
                return False

    def publication_proof(self,claim,receipt):
        from .host_boundary_recovery import assert_open
        assert_open(self.common,claim.request_id)
        if self.attempt_liveness is not None:
            from .attempt_liveness import assert_live
            assert_live(self.common,claim)
        receipt.validate(claim)
        with self.transaction():
            saved=self.common.db.execute('SELECT * FROM resident_publication_checkpoints WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if saved:
                if saved['receipt_json']!=json.dumps(asdict(receipt),sort_keys=True):raise ValueError('CHECKPOINT_RECEIPT_CHANGED')
                return json.loads(saved['proof_json'])
        proof=self.verify_publication(claim,receipt)
        if (not isinstance(proof,dict) or proof.get('publication',{}).get('verified') is not True
                or proof.get('publication',{}).get('requestId')!=claim.request_id
                or proof.get('sourceReceipt',{}).get('requestId')!=claim.request_id):
            raise ValueError('SERVER_PUBLICATION_UNVERIFIED')
        return proof

    def _save_checkpoint(self,claim,receipt,proof):
        from .host_boundary_recovery import assert_open
        assert_open(self.common,claim.request_id)
        if self.attempt_liveness is not None:
            from .attempt_liveness import assert_live
            assert_live(self.common,claim)
        self.common.db.execute('INSERT OR IGNORE INTO resident_publication_checkpoints VALUES(?,?,?)',
            (claim.resource_grant,json.dumps(asdict(receipt),sort_keys=True),json.dumps(proof,sort_keys=True)))
        saved=self.common.db.execute('SELECT receipt_json FROM resident_publication_checkpoints WHERE grant_id=?',(claim.resource_grant,)).fetchone()[0]
        if saved!=json.dumps(asdict(receipt),sort_keys=True):raise ValueError('CHECKPOINT_RECEIPT_CHANGED')

    def checkpoint(self,claim,receipt):
        proof=self.publication_proof(claim,receipt)
        with self.transaction():
            if self.failure_fence is not None:self.failure_fence.assert_publication_allowed(claim)
            lease=self._lease(claim);self.common.owned(claim.request_id,lease)
            self._publication_pending(claim)
            publication=self.common.db.execute('SELECT receipt_json FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if publication and proof.get('reservedReceipt')!=json.loads(publication['receipt_json']):raise ValueError('SERVER_RESERVED_RECEIPT_MISMATCH')
            self._save_checkpoint(claim,receipt,proof)

    def complete(self, claim, receipt, cleanup_done):
        proof=self.publication_proof(claim,receipt)
        with self.transaction():
            if self.failure_fence is not None:self.failure_fence.assert_publication_allowed(claim)
            lease = self._lease(claim)
            self._publication_pending(claim)
            publication=self.common.db.execute('SELECT prepared_at,receipt_json FROM resident_publications WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if publication and proof.get('reservedReceipt')!=json.loads(publication['receipt_json']):
                raise ValueError('SERVER_RESERVED_RECEIPT_MISMATCH')
            self._save_checkpoint(claim,receipt,proof)
            return self.common.transition(claim.request_id, 'resident.complete.'+claim.attempt_id,
                'completed' if cleanup_done else 'sync_pending', stage='finalize', lease=lease,
                receipt=proof['sourceReceipt'], publication=proof['publication'],
                details={'resident':True,'cleanupDone':bool(cleanup_done)},
                **({'recorded_at':publication['prepared_at']} if publication else {}))

    def _block(self, claim, key, code, details, stage=None):
        if self.verify_stopped(claim) is not True:
            raise ValueError('EXECUTION_STOP_NOT_VERIFIED')
        with self.transaction():
            lease=self._lease(claim)
            self._publication_pending(claim)
            row=self.common.row(claim.request_id)
            if (row['state'] in ('processing','synchronizing') and row['lease_until'] is not None
                    and row['lease_until']<=self.common.clock()):
                # Caller only reports after execute()/all children have exited.
                # Exact current fence+nonce+worker checked by the official API.
                self.common.block_expired(claim.request_id,key,lease,code)
                self.common.event(claim.request_id,key+'.details','resident_stopped',
                                  'blocked','blocked',details)
                return self.common.view(claim.request_id)
            return self.common.transition(claim.request_id,key,'blocked',stage=stage,
                                           lease=lease,error=code,details=details)

    def fail(self, claim, code, cleanup_done):
        return self._block(claim,'resident.fail.'+claim.attempt_id,code,
                           {'cleanupDone':bool(cleanup_done),'resident':True})

    def uncertain(self, claim, code):
        return self._block(claim,'resident.uncertain.'+claim.attempt_id,code,
                           {'publicationUnknown':True,'regenerate':False},'reconcile')

    def execution_unknown(self,claim,code):
        with self.transaction():
            self._lease(claim)
            row=self.common.row(claim.request_id)
            self.common.event(claim.request_id,'resident.execution-unknown.'+claim.attempt_id,
                'execution_unknown',row['state'],row['state'],
                {'errorCode':code,'executionStopped':False,'regenerate':False})

    def execution_closure(self, claim):
        if self.verify_stopped(claim) is True:
            return {'kind':'terminal','grantId':claim.resource_grant}
        port=self.timeout_disposition
        if port is not None and port.verify(claim) is True:
            return {'kind':'timeout-service-quiescent','grantId':claim.resource_grant,
                    'receiptHash':port.receipt_hash(claim)}
        return None

    def release_resource(self, claim):
        with self.transaction():
            self._issued_lease(claim,allow_released=True)
            row=self.common.db.execute('SELECT released FROM resident_resource_grants WHERE grant_id=?',(claim.resource_grant,)).fetchone()
            if row['released']:return
            self._publication_pending(claim)
            observed_fence=self.common.row(claim.request_id)['fence']
        # OS/provider probes may block; do not hold the shared DB write lock.
        # The issued grant stays allocated throughout the probe.
        if self.execution_closure(claim) is None:
            raise ValueError('EXECUTION_STOP_NOT_VERIFIED')
        with self.transaction():
            self._issued_lease(claim,allow_released=True)
            self._publication_pending(claim)
            if self.common.row(claim.request_id)['fence']!=observed_fence:
                raise ValueError('RESOURCE_FENCE_CHANGED_DURING_PROBE')
            self.common.db.execute('UPDATE resident_resource_grants SET released=1 WHERE grant_id=?',
                                   (claim.resource_grant,))

    def keepalive_lost(self,claim,reason):
        if self.attempt_liveness is None:raise ValueError('ATTEMPT_LIVENESS_NOT_CONFIGURED')
        if reason not in ('renew-rejected','renew-unavailable'):raise ValueError('KEEPALIVE_REASON_INVALID')
        return self.attempt_liveness.expire(claim)
