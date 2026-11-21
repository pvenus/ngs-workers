"""Service-only failed NPC closure fence. No cancellation, generation or deletion.

Tracking starts explicitly inside NEW request acceptance/claim transactions.
Missing old rows are UNKNOWN, never proof that publication did not start.
All request admission/resume and every publication path must honor these fences.
"""
from dataclasses import asdict
import hashlib
import json
from ..contracts import Claim, Receipt


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class FailureFence:
    def __init__(self, bridge, guard_reader, *, operation_scope=None, publication_reader=None, frame_no_post_reader=None):
        if not callable(guard_reader): raise ValueError('FAILURE_GUARD_READER_REQUIRED')
        self.bridge, self.common, self.guard_reader = bridge, bridge.common, guard_reader
        self.operation_scope, self.publication_reader = operation_scope, publication_reader
        self.frame_no_post_reader=frame_no_post_reader
        if operation_scope is not None and not callable(operation_scope):raise ValueError("FAILURE_OPERATION_READER_REQUIRED")

    def initialize_schema(self):
        with self.bridge.transaction():
            for sql in (
                'CREATE TABLE IF NOT EXISTS resident_failure_tracking(request_id TEXT PRIMARY KEY,publication_started INTEGER NOT NULL DEFAULT 0)',
                'CREATE TABLE IF NOT EXISTS resident_failure_grants(grant_id TEXT PRIMARY KEY,publication_started INTEGER NOT NULL DEFAULT 0)',
                'CREATE TABLE IF NOT EXISTS resident_failure_fences(request_id TEXT PRIMARY KEY,parent_request_id TEXT NOT NULL,proof_hash TEXT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS resident_failure_proofs(parent_request_id TEXT PRIMARY KEY,snapshot_hash TEXT NOT NULL,proof_json TEXT NOT NULL)'):
                self.common.db.execute(sql)

    def _has(self, table):
        return self.common.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def assert_claim_allowed(self, request_id):
        # Called inside the authority transaction. Include direct ancestors, so
        # newly attached children cannot bypass a fenced parent scope.
        visited = set(); current = request_id
        while current and current not in visited:
            visited.add(current)
            if self.common.db.execute('SELECT 1 FROM resident_failure_fences WHERE request_id=?', (current,)).fetchone():
                raise ValueError('FAILURE_SCOPE_FENCED')
            row = self.common.db.execute('SELECT parent_id FROM common_requests WHERE request_id=?', (current,)).fetchone()
            current = row[0] if row else None

    def assert_publication_allowed(self, claim):
        self.assert_claim_allowed(claim.request_id)

    def track_request(self, request_id):
        """Only the new-accept branch may call this, in its existing transaction."""
        self.assert_claim_allowed(request_id)
        row = self.common.row(request_id)
        if row['state'] != 'queued' or row['fence'] != 0 or row['source_receipt']:
            raise ValueError('FAILURE_TRACKING_NEW_REQUEST_REQUIRED')
        self.common.db.execute('INSERT INTO resident_failure_tracking VALUES(?,0)', (request_id,))

    def track_claim(self, claim):
        self.assert_claim_allowed(claim.request_id)
        self.common.db.execute('INSERT INTO resident_failure_grants VALUES(?,0)', (claim.resource_grant,))

    def mark_parent_publication_started(self, request_id):
        with self.bridge.transaction():
            self.assert_claim_allowed(request_id)
            # An untracked legacy attempt stays unknown, even after this marker.
            self.common.db.execute('UPDATE resident_failure_tracking SET publication_started=1 WHERE request_id=?', (request_id,))

    def mark_publication_started(self, claim):
        with self.bridge.transaction():
            self.bridge._lease(claim)
            self.assert_publication_allowed(claim)
            self.common.db.execute('UPDATE resident_failure_grants SET publication_started=1 WHERE grant_id=?', (claim.resource_grant,))
            self.common.db.execute('UPDATE resident_failure_tracking SET publication_started=1 WHERE request_id=?', (claim.request_id,))

    def _closure(self, parent):
        found = {parent}
        while True:
            before = len(found)
            for rid in tuple(found):
                found.update(r[0] for r in self.common.db.execute('SELECT request_id FROM common_requests WHERE parent_id=?', (rid,)))
                if self._has('npc_single_invocations'):
                    found.update(r[0] for r in self.common.db.execute('SELECT child_id FROM npc_single_invocations WHERE parent_id=? AND child_id IS NOT NULL', (rid,)))
                if self._has('npc_single_duplicate_retries'):
                    found.update(r[0] for r in self.common.db.execute('SELECT old_child_id FROM npc_single_duplicate_retries WHERE parent_id=? AND old_child_id IS NOT NULL', (rid,)))
            if len(found) > 10000: raise ValueError('FAILURE_SCOPE_TOO_LARGE')
            if len(found) == before: return sorted(found)

    def _snapshot(self, parent):
        if self.operation_scope is not None:
            scoped = self.operation_scope(self.guard_reader(parent))
            if scoped is not None:
                return self._operation_snapshot(parent, scoped)
        ids = self._closure(parent); result = {'requests': [], 'grants': [], 'bindings': [], 'references': [], 'batches': [], 'invocations': [], 'duplicateRetries': [], 'tracking': [], 'grantTracking': []}
        for rid in ids:
            row = dict(self.common.row(rid)); result['requests'].append(row)
            if row['state'] not in ('blocked', 'failed', 'conflict', 'discarded') or row['worker']:
                raise ValueError('FAILURE_DESCENDANT_NOT_TERMINAL')
            if row.get('source_receipt') or row.get('publication_receipt'):
                raise ValueError('FAILURE_PUBLICATION_EXISTS')
            prepost=(row['request_id']==parent and row.get('request_type')=='npc.animation.frames.generate' and row.get('schema_version')=='ngs.frame-intake.v1' and row.get('parent_id') is None and callable(self.frame_no_post_reader))
            tracked = self.common.db.execute('SELECT * FROM resident_failure_tracking WHERE request_id=?', (rid,)).fetchone()
            if tracked is None or tracked['publication_started'] != 0 and not (prepost and tracked['publication_started']==1):
                raise ValueError('FAILURE_PUBLICATION_HISTORY_UNKNOWN')
            result['tracking'].append(dict(tracked))
            result['references'].extend(dict(r) for r in self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=? ORDER BY domain,schema_version,reference_id', (rid,)))
            if self._has('npc_single_batches'):
                result['batches'].extend(dict(r) for r in self.common.db.execute('SELECT * FROM npc_single_batches WHERE id=?', (rid,)))
            if self._has('npc_single_invocations'):
                invocations = [dict(r) for r in self.common.db.execute('SELECT * FROM npc_single_invocations WHERE parent_id=? ORDER BY invocation_id', (rid,))]
                if any(r.get('dispatched') and not r.get('child_id') for r in invocations):
                    raise ValueError('FAILURE_CHILD_DISPATCH_UNKNOWN')
                if any(r.get('receipt_json') for r in invocations):
                    raise ValueError('FAILURE_CHILD_RECEIPT_EXISTS')
                result['invocations'].extend(invocations)
            if self._has('npc_single_duplicate_retries'):
                retries = [dict(r) for r in self.common.db.execute('SELECT * FROM npc_single_duplicate_retries WHERE parent_id=? ORDER BY invocation_id,attempt', (rid,))]
                if any(r.get('old_receipt_json') for r in retries): raise ValueError('FAILURE_CHILD_RECEIPT_EXISTS')
                result['duplicateRetries'].extend(retries)
            grants = self.common.db.execute('SELECT * FROM resident_resource_grants WHERE request_id=? ORDER BY grant_id', (rid,)).fetchall()
            if row['fence'] > 0 and rid != parent and not any(
                    json.loads(g['claim_json']).get('fencing_token') == str(row['fence']) for g in grants):
                raise ValueError('FAILURE_EXECUTION_HISTORY_UNKNOWN')
            for grant in grants:
                item = dict(grant); result['grants'].append(item); gid = grant['grant_id']
                tracking = self.common.db.execute('SELECT * FROM resident_failure_grants WHERE grant_id=?', (gid,)).fetchone()
                if tracking is None or tracking['publication_started'] != 0 and not (prepost and tracking['publication_started']==1):
                    raise ValueError('FAILURE_GRANT_HISTORY_UNKNOWN')
                result['grantTracking'].append(dict(tracking))
                for table in ('resident_publications', 'resident_publication_checkpoints'):
                    if self.common.db.execute('SELECT 1 FROM '+table+' WHERE grant_id=?', (gid,)).fetchone():
                        raise ValueError('FAILURE_PUBLICATION_RESERVED')
                result['bindings'].extend(dict(r) for r in self.common.db.execute('SELECT * FROM resident_claim_bindings WHERE grant_id=?', (gid,)))
        parent_row = next(r for r in result['requests'] if r['request_id'] == parent)
        if parent_row['state'] not in ('blocked', 'failed', 'conflict'):
            raise ValueError('FAILURE_PARENT_STATE_INVALID')
        batch = next((r for r in result['batches'] if r['id'] == parent), None)
        refs = [r for r in result['references'] if r['request_id'] == parent]
        if (parent_row.get('request_type')=='npc.animation.frames.generate'
                and parent_row['schema_version']=='ngs.frame-intake.v1'):
            # Only standalone, formally accepted frame requests. Never infer
            # absence of work for legacy/untracked rows or an aggregate child.
            if parent_row['parent_id'] is not None or len(result['requests'])!=1:
                raise ValueError('FAILURE_FRAME_PARENT_SCOPE_UNSUPPORTED')
            if not self._has('frame_intake_requests'):
                raise ValueError('FAILURE_FRAME_INTAKE_MISSING')
            intake=self.common.db.execute('SELECT * FROM frame_intake_requests WHERE id=?',(parent,)).fetchone()
            if (not intake or intake['actor']!=parent_row['requester']
                    or intake['receipt'] is not None or intake['publication'] is not None):
                raise ValueError('FAILURE_FRAME_INTAKE_UNPROVEN')
            request=json.loads(intake['body'])
            if (request.get('schemaVersion')!='ngs.npc-animation-frames-request.v1'
                    or request.get('projectId')!=parent_row['project_id']
                    or digest(request)!=intake['input_hash']
                    or len(refs)!=1 or refs[0]['domain']!='frames.requests'
                    or refs[0]['schema_version']!='ngs.frame-intake.v1'
                    or refs[0]['reference_id']!=parent or refs[0]['input_hash']!=intake['input_hash']):
                raise ValueError('FAILURE_FRAME_INTAKE_CHANGED')
            bindings=result['bindings']
            if any(json.loads(row['binding_json']).get('targetId')!=request.get('npcId') for row in bindings):
                raise ValueError('FAILURE_FRAME_TARGET_CHANGED')
            if any(t['publication_started'] for t in result['tracking']):
                if (len(result['grants'])!=1 or result['grants'][0]['released']!=1 or str(parent_row['fence'])!=json.loads(result['grants'][0]['claim_json'])['fencing_token'] or any(t['publication_started']!=1 for t in result['grantTracking'])):raise ValueError('FAILURE_FRAME_PREPOST_SCOPE')
            result['frameIntake']=dict(intake)
            return result
        if (parent_row.get('schema_version') != 'ngs.npc-single-batch.v1' or batch is None
                or not any(r['domain'] == 'npc.single-batch' and r['schema_version'] == 'ngs.npc-single-batch.v1'
                           and r['reference_id'] == parent and r['input_hash'] == batch['input_hash'] for r in refs)):
            raise ValueError('FAILURE_PARENT_ROUTE_UNSUPPORTED')
        request = json.loads(batch['request_json']); selection = json.loads(batch['selection_json'])
        stages = selection.get('stages', [])
        if (digest(request) != batch['input_hash'] or selection.get('requestHash') != batch['input_hash']
                or len(stages) != 1 or stages[0].get('id') not in ('overview','art','category-add','category-modify')
                or stages[0].get('candidateCount') != 1 or len(selection.get('invocations', [])) != 1
                or selection.get('registry', {}).get('selectionPolicy', {}).get('executionContract') != 'worker-count.v1'):
            raise ValueError('FAILURE_PARENT_ROUTE_UNSUPPORTED')
        return result

    def _resolved_finalize(self, row, grants):
        """A durable supervisor FINALIZE lease is not a second model execution."""
        if row['state']!='completed' or row['worker'] is not None or not self._has('resident_reconciliations'):
            return None
        matches=[]
        for grant in grants:
            gid=grant['grant_id'];claim=json.loads(grant['claim_json'])
            rec=self.common.db.execute('SELECT * FROM resident_reconciliations WHERE grant_id=?',(gid,)).fetchone()
            if not rec or rec['state']!='resolved' or rec['last_code']!='RECOVERED' or not rec['recovery_json']:continue
            lease=json.loads(rec['recovery_json'])
            if lease.get('fence')!=row['fence']:continue
            def need(ok):
                if not ok:raise ValueError('FAILURE_RECONCILIATION_CHANGED')
            need(grant['released']==1 and claim['request_id']==row['request_id'] and claim['resource_grant']==gid)
            need(lease.get('requestId')==row['request_id'] and lease.get('workerId')=='resident-reconciler' and int(claim['fencing_token'])<lease['fence'])
            checkpoint=self.common.db.execute('SELECT * FROM resident_publication_checkpoints WHERE grant_id=?',(gid,)).fetchone()
            need(checkpoint is not None and rec['receipt_json'] is not None and json.loads(rec['receipt_json'])==json.loads(checkpoint['receipt_json']))
            proof=json.loads(checkpoint['proof_json'])
            need(proof.get('sourceReceipt',{}).get('requestId')==row['request_id'] and proof.get('publication',{}).get('verified')is True)
            for column,key in (('source_receipt','sourceReceipt'),('publication_receipt','publication')):
                compact=json.loads(row[column]) if row[column] else {}
                need(compact.get('requestId')==row['request_id'] and compact.get('receiptHash')==digest(proof[key]))
            events=self.common.db.execute("SELECT * FROM common_events WHERE request_id=? AND kind IN ('claimed','lease_recovered')",(row['request_id'],)).fetchall()
            claims=[e for e in events if json.loads(e['details_json']).get('fence')==lease['fence']]
            need(len(claims)==1)
            event=claims[0];detail=json.loads(event['details_json'])
            need(event['event_key'].startswith('resident.reconcile.claim.'+gid+'.') and detail=={'fence':lease['fence'],'stage':'finalize','worker':lease['workerId']} and event['to_state']=='synchronizing')
            finish=self.common.db.execute('SELECT * FROM common_events WHERE request_id=? AND event_key=?',(row['request_id'],'resident.reconcile.finish.'+gid+'.'+str(lease['fence']))).fetchone()
            need(finish is not None and finish['kind']=='transition' and finish['from_state']=='synchronizing' and finish['to_state']=='completed')
            details=json.loads(finish['details_json'])
            need(details.get('reconciled')is True and details.get('cleanupDone')is True and details.get('generationCalls')==0 and details.get('sourceAttemptId')==claim['attempt_id'] and details.get('sourceFence')==claim['fencing_token'])
            matches.append((lease,dict(rec),dict(event),dict(finish)))
        if len(matches)>1:raise ValueError('FAILURE_RECONCILIATION_CHANGED')
        return matches[0] if matches else None

    def _operation_snapshot(self, parent, scoped):
        """Strict opt-in domain scope; never infer a media route from a prefix."""
        from ..deployment.resident_operation_proof import OperationScope
        guard = self.guard_reader(parent)
        if not isinstance(scoped, OperationScope) or scoped.guard != guard:
            raise ValueError('FAILURE_OPERATION_SCOPE_CHANGED')
        if not isinstance(scoped.binding_hash,str) or len(scoped.binding_hash)!=64:
            raise ValueError('FAILURE_OPERATION_SCOPE_HASH')
        ids = self._closure(parent)
        if set(ids) != set(scoped.request_ids) | {parent}:
            raise ValueError('FAILURE_OPERATION_CLOSURE_CHANGED')
        out = {'requests': [], 'grants': [], 'bindings': [], 'tracking': [],
               'grantTracking': [], 'references': [], 'publications': [],
               'checkpoints': [], 'domainRows': [], 'scopeHash': scoped.binding_hash}
        for rid in ids:
            row = dict(self.common.row(rid)); out['requests'].append(row)
            if row['worker'] or row['state'] not in ('blocked','failed','conflict','discarded','completed','queued'):
                raise ValueError('FAILURE_DESCENDANT_NOT_TERMINAL')
            if row['state'] == 'queued' and (rid == parent or row['fence'] != 0):
                raise ValueError('FAILURE_QUEUED_EXECUTION_UNKNOWN')
            # Do not trust logical invocations alone: issued descendants must
            # physically reach this exact original ownership parent.
            cursor = rid; seen = set()
            while cursor != parent:
                if not cursor or cursor in seen:raise ValueError('FAILURE_OPERATION_PARENT_CHANGED')
                seen.add(cursor);cursor = self.common.row(cursor)['parent_id']
            tracked = self.common.db.execute('SELECT * FROM resident_failure_tracking WHERE request_id=?',(rid,)).fetchone()
            if tracked is None:raise ValueError('FAILURE_PUBLICATION_HISTORY_UNKNOWN')
            out['tracking'].append(dict(tracked))
            out['references'].extend(dict(r) for r in self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=? ORDER BY domain,schema_version,reference_id',(rid,)))
            for table, column in (('npc_single_batches','id'),('pose_intake_requests','id'),('pose_direction_requests','id'),('animation_intake_requests','id'),('animation_batch_state','id')):
                if self._has(table):
                    out['domainRows'].extend({'table':table,'row':dict(r)} for r in self.common.db.execute('SELECT * FROM '+table+' WHERE '+column+'=?',(rid,)))
            if self._has('npc_single_invocations'):
                invocations = self.common.db.execute('SELECT * FROM npc_single_invocations WHERE parent_id=? ORDER BY invocation_id',(rid,)).fetchall()
                if any(r['dispatched'] and not r['child_id'] for r in invocations):raise ValueError('FAILURE_CHILD_DISPATCH_UNKNOWN')
                out['domainRows'].extend({'table':'npc_single_invocations','row':dict(r)} for r in invocations)
            grants = self.common.db.execute('SELECT * FROM resident_resource_grants WHERE request_id=? ORDER BY grant_id',(rid,)).fetchall()
            if row['state']=='queued' and grants:raise ValueError('FAILURE_QUEUED_EXECUTION_UNKNOWN')
            finalized=self._resolved_finalize(row,grants)
            if finalized:
                out['domainRows'].extend({'table':name,'row':value} for name,value in zip(('resident_reconciliations','common_events','common_events'),finalized[1:]))
            if rid != parent and row['fence'] and not finalized and not any(json.loads(r['claim_json']).get('fencing_token')==str(row['fence']) for r in grants):
                raise ValueError('FAILURE_EXECUTION_HISTORY_UNKNOWN')
            if self._has('common_events') and (rid != parent or grants):
                issued={(json.loads(r['claim_json'])['fencing_token'],dict(r).get('worker_id')) for r in grants}
                for event in self.common.db.execute("SELECT details_json FROM common_events WHERE request_id=? AND kind IN ('claimed','lease_recovered')",(rid,)):
                    detail=json.loads(event[0])
                    if finalized and detail=={'fence':finalized[0]['fence'],'stage':'finalize','worker':finalized[0]['workerId']}:continue
                    if (str(detail.get('fence')),detail.get('worker')) not in issued:
                        raise ValueError('FAILURE_EXECUTION_HISTORY_UNKNOWN')
            settled_for_request = []
            for grant in grants:
                item = dict(grant); gid = item['grant_id']; out['grants'].append(item)
                claim = json.loads(item['claim_json'])
                if claim.get('request_id') != rid or claim.get('resource_grant') != gid or claim.get('attempt_id') != item['attempt_id']:
                    raise ValueError('FAILURE_ISSUED_CLAIM_CHANGED')
                binding = self.common.db.execute('SELECT * FROM resident_claim_bindings WHERE grant_id=?',(gid,)).fetchone()
                if binding is None:raise ValueError('FAILURE_BINDING_UNKNOWN')
                out['bindings'].append(dict(binding)); bound=json.loads(binding['binding_json'])
                if bound.get('targetId')!=guard['npcId'] or bound.get('expectedRevision')!=claim['expected_revision']:
                    raise ValueError('FAILURE_BINDING_CHANGED')
                tracking = self.common.db.execute('SELECT * FROM resident_failure_grants WHERE grant_id=?',(gid,)).fetchone()
                if tracking is None:raise ValueError('FAILURE_GRANT_HISTORY_UNKNOWN')
                out['grantTracking'].append(dict(tracking))
                reservation = self.common.db.execute('SELECT * FROM resident_publications WHERE grant_id=?',(gid,)).fetchone()
                checkpoint = self.common.db.execute('SELECT * FROM resident_publication_checkpoints WHERE grant_id=?',(gid,)).fetchone()
                if not tracking['publication_started'] and reservation is None and checkpoint is None:continue
                if checkpoint is None or (reservation is not None and (reservation['state']!='committed' or not reservation['receipt_json'])):
                    raise ValueError('FAILURE_PUBLICATION_UNSETTLED')
                receipt=json.loads(checkpoint['receipt_json']); proof=json.loads(checkpoint['proof_json'])
                typed_claim={**claim,'input_refs':tuple(claim['input_refs'])}
                typed_receipt={**receipt,'asset_refs':tuple(receipt.get('asset_refs',()))}
                Receipt(**typed_receipt).validate(Claim(**typed_claim))
                if any(receipt.get(k)!=claim[k] for k in ('request_id','attempt_id','fencing_token')) or proof.get('sourceReceipt',{}).get('requestId')!=rid or proof.get('publication',{}).get('verified') is not True:
                    raise ValueError('FAILURE_CHECKPOINT_CHANGED')
                if reservation is not None:
                    raw = json.loads(reservation['receipt_json'])
                    expected={'requestId':rid,'attemptId':claim['attempt_id'],'fencingToken':claim['fencing_token'],'publicationHash':reservation['publication_hash'],'authority':'filex','status':'committed','verified':True,
                              'capability':claim['capability'],'projectId':bound.get('projectId'),
                              'targetId':guard['npcId'],'expectedRevision':claim['expected_revision'],'requestHash':digest(claim['parameters'])}
                    if any(raw.get(k)!=v for k,v in expected.items()) or raw.get('receiptHash')!=digest({k:v for k,v in raw.items() if k!='receiptHash'}):
                        raise ValueError('FAILURE_PUBLICATION_RECEIPT_CHANGED')
                    out['publications'].append(dict(reservation))
                out['checkpoints'].append(dict(checkpoint));settled_for_request.append(gid)
            if (tracked['publication_started'] or row.get('source_receipt') or row.get('publication_receipt') or row['state']=='completed') and not settled_for_request:
                # Parent-only final CAS requires a separately implemented durable
                # receipt contract; absent grants never mean 'nothing happened'.
                raise ValueError('FAILURE_PARENT_PUBLICATION_UNPROVEN')
        root = next(r for r in out['requests'] if r['request_id']==parent)
        if root['state'] not in ('blocked','failed','conflict'):raise ValueError('FAILURE_PARENT_STATE_INVALID')
        return out

    def _settled_publications(self, snapshot):
        settled=[]
        for checkpoint in snapshot.get('checkpoints',[]):
            if not callable(self.publication_reader):raise ValueError('FAILURE_LIVE_PUBLICATION_READER_REQUIRED')
            gid=checkpoint['grant_id'];grant=next(g for g in snapshot['grants'] if g['grant_id']==gid)
            raw=json.loads(grant['claim_json']);raw['input_refs']=tuple(raw['input_refs']);claim=Claim(**raw)
            reserved=next((p for p in snapshot['publications'] if p['grant_id']==gid),None)
            envelope=json.loads(reserved['receipt_json']) if reserved else None
            proof=json.loads(checkpoint['proof_json'])
            # Pair/pose reservations carry Request envelopes, not the canonical
            # Filex receipt itself. Animation may use only a durable checkpoint.
            expected=(envelope.get('canonicalReceipt') or envelope.get('sourceReceipt') or envelope) if envelope else proof['sourceReceipt']
            if not isinstance(expected,dict) or not expected or (expected.get('verified') is not True and expected.get('status')!='committed'):raise ValueError('FAILURE_CANONICAL_RECEIPT_REQUIRED')
            actual=self.publication_reader(claim,expected)
            if actual!=expected:raise ValueError('FAILURE_LIVE_PUBLICATION_CHANGED')
            settled.append({'grantId':gid,'requestId':claim.request_id,'attemptId':claim.attempt_id,
                'fencingToken':claim.fencing_token,'checkpointHash':digest(checkpoint),
                'publicationHash':reserved['publication_hash'] if reserved else digest(expected),
                'receiptHash':envelope['receiptHash'] if envelope else digest(expected),
                'evidenceKind':'committed-reservation' if reserved else 'checkpoint-pds'})
        return settled

    def _guard(self, guard):
        keys = ('npcId', 'generationId', 'requestId')
        if not isinstance(guard, dict) or any(not isinstance(guard.get(k), str) or not guard[k] for k in keys):
            raise ValueError('FAILURE_SCOPE_GUARD_INVALID')
        expected = self.guard_reader(guard['requestId'])
        if not isinstance(expected, dict) or any(expected.get(k) != guard[k] for k in keys):
            raise ValueError('FAILURE_SCOPE_GUARD_MISMATCH')
        if expected.get('leaseToken') is not None and expected['leaseToken'] != guard.get('leaseToken'):
            raise ValueError('FAILURE_SCOPE_GUARD_MISMATCH')
        return {k: guard[k] for k in keys}

    def seal_for_failure(self, guard):
        scope = self._guard(guard); parent = scope['requestId']
        with self.bridge.transaction():
            snapshot = self._snapshot(parent)
            old = self.common.db.execute('SELECT * FROM resident_failure_proofs WHERE parent_request_id=?', (parent,)).fetchone()
            if old:
                if old['snapshot_hash'] != digest(snapshot): raise ValueError('FAILURE_SCOPE_CHANGED')
                proof = json.loads(old['proof_json'])
                if any(proof[k] != scope[k] for k in scope): raise ValueError('FAILURE_SCOPE_GUARD_MISMATCH')
                return proof
        # OS/provider checks are outside the SQLite writer transaction. Re-read
        # the entire closure after them before atomically closing publication.
        closures=[]
        for grant in snapshot['grants']:
            raw = json.loads(grant['claim_json']); raw['input_refs'] = tuple(raw['input_refs'])
            c=Claim(**raw);reader=getattr(self.bridge,'execution_closure',None)
            closure=reader(c) if callable(reader) else ({'kind':'terminal','grantId':c.resource_grant} if self.bridge.verify_stopped(c)is True else None)
            if (not isinstance(closure,dict) or closure.get('grantId')!=grant['grant_id']
                or closure.get('kind') not in ('terminal','timeout-service-quiescent')):raise ValueError('FAILURE_EXECUTION_STOP_UNCONFIRMED')
            closures.append(closure)
        no_post=None
        if 'frameIntake'in snapshot and any(t['publication_started'] for t in snapshot['tracking']):
            if not callable(self.frame_no_post_reader):raise ValueError('FAILURE_FRAME_NO_POST_READER_REQUIRED')
            no_post=self.frame_no_post_reader(snapshot)
            if (not isinstance(no_post,dict) or no_post.get('schemaVersion')!='resident.frame-no-post.v1' or no_post.get('evidenceHash')!=digest({k:v for k,v in no_post.items()if k!='evidenceHash'}) or no_post.get('snapshotHash')!=digest(snapshot)):
                raise ValueError('FAILURE_FRAME_NO_POST_UNVERIFIED')
        settled = self._settled_publications(snapshot)
        with self.bridge.transaction():
            if self._guard(guard) != scope or digest(self._snapshot(parent)) != digest(snapshot):
                raise ValueError('FAILURE_SCOPE_CHANGED')
            root = next(r for r in snapshot['requests'] if r['request_id'] == parent)
            proof = {**scope, 'state': root['state'], 'revision': root['revision'],
                'allExecutionsStopped': all(c['kind']=='terminal' for c in closures), 'publicationNeverStarted': not bool(settled),
                'publicationFenced': True, 'unresolvedReservations': 0,
                'descendantsIneligible': True, 'snapshotHash': digest(snapshot)}
            if no_post is not None:proof.update(publicationAttemptMarker=True,noPostEvidence=no_post)
            if any(c['kind']=='timeout-service-quiescent' for c in closures):
                terminal={c['grantId'] for c in closures if c['kind']=='terminal'}
                if (root['request_type']!='npc.animation.generate-eight-directions'
                    or len({c['grantId'] for c in closures})!=len(closures)
                    or any(x['grantId'] not in terminal for x in settled)):
                    raise ValueError('TIMEOUT_DISPOSITION_PUBLICATION_FORBIDDEN')
                proof.update(executionClosureFamily='animation',executionClosureKind='animation-timeout-quiescent.v1',allExecutionsClosed=True,executionClosures=closures)
            if settled:
                proof.update(publicationSettled=True,settledPublications=settled)
            proof['ledgerProofHash'] = digest(proof)
            for row in snapshot['requests']:
                old = self.common.db.execute('SELECT parent_request_id FROM resident_failure_fences WHERE request_id=?', (row['request_id'],)).fetchone()
                if old and old[0] != parent: raise ValueError('FAILURE_SCOPE_ALREADY_FENCED')
                self.common.db.execute('INSERT OR IGNORE INTO resident_failure_fences VALUES(?,?,?)', (row['request_id'],parent,proof['ledgerProofHash']))
            self.common.db.execute('INSERT INTO resident_failure_proofs VALUES(?,?,?)', (parent,digest(snapshot),json.dumps(proof,sort_keys=True)))
            return proof

    def failure_reader(self, guard):
        return self.seal_for_failure(guard)
