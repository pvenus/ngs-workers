"""Service-owned parent closure fence + live native revocation proof.

No stop claim, resource release, cleanup, retry submission or direct source write.
All callbacks are server ports; caller supplies only parent ID and authenticated actor.
"""
import json,hashlib
from dataclasses import asdict
from .attempt_liveness import digest,timeout

def request_open(common,rid):
 if common.db.execute("SELECT 1 FROM sqlite_master WHERE name='resident_logical_fence_members'").fetchone()and common.db.execute('SELECT 1 FROM resident_logical_fence_members WHERE request_id=?',(rid,)).fetchone():raise ValueError('NATIVE_PARENT_SCOPE_REVOKED')

class LogicalRetryAuthority:
 def __init__(self,bridge,*,parent_reader,revoke,read_revocation):
  if not all(callable(x)for x in (parent_reader,revoke,read_revocation)):raise ValueError('LOGICAL_RETRY_SERVER_PORTS_REQUIRED')
  self.bridge,self.common=bridge,bridge.common;self.parent_reader,self.revoke,self.read_revocation=parent_reader,revoke,read_revocation
 def initialize_schema(self):
  with self.bridge.transaction():
   self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_logical_closures(parent_id TEXT PRIMARY KEY,closure_json TEXT NOT NULL,receipt_json TEXT)')
   self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_logical_fence_members(request_id TEXT PRIMARY KEY,parent_id TEXT NOT NULL)')
 def _closure(self,rid,actor):
  from ..contracts import Claim
  value=self.parent_reader(rid,actor)
  if not isinstance(value,dict)or set(value)!={'parentRequestId','actorId','parentGuard','requestIds','bindingHash'}or value['parentRequestId']!=rid or value['actorId']!=actor:raise ValueError('LOGICAL_PARENT_BINDING')
  guard=value['parentGuard'];ids=value['requestIds'];row=self.common.row(rid)
  if row['requester']!=actor or row['state']not in ('blocked','failed','conflict')or not isinstance(ids,list)or rid not in ids or len(ids)!=len(set(ids)):raise ValueError('LOGICAL_PARENT_STATE')
  if set(guard)!={'npcId','generationId','requestId','leaseToken'}or guard['requestId']!=rid:raise ValueError('LOGICAL_NATIVE_GUARD')
  attempts=[];expired=False;publication='never-started'
  for child in sorted(ids):
   current=self.common.row(child)
   if current['requester']!=actor:raise ValueError('LOGICAL_CLOSURE_ACTOR')
   for g in self.common.db.execute('SELECT * FROM resident_resource_grants WHERE request_id=? ORDER BY grant_id',(child,)):
    v=json.loads(g['claim_json']);v['input_refs']=tuple(v['input_refs']);c=Claim(**v);self.bridge._issued_lease(c,allow_released=True)
    expired=expired or timeout(self.common,c)is not None
    tracking=self.common.db.execute('SELECT publication_started FROM resident_failure_grants WHERE grant_id=?',(c.resource_grant,)).fetchone()
    reservation=self.common.db.execute('SELECT 1 FROM resident_publications WHERE grant_id=?',(c.resource_grant,)).fetchone()
    checkpoint=self.common.db.execute('SELECT 1 FROM resident_publication_checkpoints WHERE grant_id=?',(c.resource_grant,)).fetchone()
    if tracking is None or tracking[0]!=0 or reservation or checkpoint:publication='unknown'
    attempts.append({'requestId':child,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'grantId':c.resource_grant,'scopeHash':digest(asdict(c))})
  if not expired:raise ValueError('LOGICAL_TIMEOUT_PROOF_REQUIRED')
  out={'schemaVersion':'resident.logical-parent-closure.v1','parentRequestId':rid,'actorId':actor,'parentGuard':guard,'requestIds':sorted(ids),'bindingHash':value['bindingHash'],'attempts':attempts,'publicationState':publication,'settledPublications':[]}
  out['closureFenceId']='closure.'+digest(out);out['authorityHash']=digest(out);return out
 def read_closure(self,rid,fence_id):
  with self.bridge.transaction():
   row=self.common.db.execute('SELECT closure_json FROM resident_logical_closures WHERE parent_id=?',(rid,)).fetchone()
   if row is None:raise ValueError('LOGICAL_CLOSURE_NOT_SEALED')
   value=json.loads(row[0])
   if value['closureFenceId']!=fence_id or self._closure(rid,value['actorId'])!=value:raise ValueError('LOGICAL_CLOSURE_CHANGED')
   return value
 def proof(self,rid,actor):
  with self.bridge.transaction():
   value=self._closure(rid,actor)
   saved=self.common.db.execute('SELECT closure_json FROM resident_logical_closures WHERE parent_id=?',(rid,)).fetchone()
   if saved and json.loads(saved[0])!=value:raise ValueError('LOGICAL_CLOSURE_CHANGED')
   if not saved:
    self.common.db.execute('INSERT INTO resident_logical_closures VALUES(?,?,NULL)',(rid,json.dumps(value,sort_keys=True)))
    for child in value['requestIds']:
     old=self.common.db.execute('SELECT parent_id FROM resident_logical_fence_members WHERE request_id=?',(child,)).fetchone()
     if old and old[0]!=rid:raise ValueError('LOGICAL_CLOSURE_OVERLAP')
     self.common.db.execute('INSERT OR IGNORE INTO resident_logical_fence_members VALUES(?,?)',(child,rid))
     current=self.common.row(child)
     if current['state']in('queued','retry_wait')and current['worker']is None:
      self.common.transition(child,'logical.parent-fenced.'+value['closureFenceId'],'blocked',error='NATIVE_PARENT_SCOPE_REVOKED',details={'executionStopped':False,'artifactsPreserved':True})
  if value['publicationState']!='never-started':raise ValueError('LOGICAL_PUBLICATION_RECONCILIATION_REQUIRED')
  receipt=self.revoke(value);live=self.read_revocation(value)
  expected={'npcId':value['parentGuard']['npcId'],'generationId':value['parentGuard']['generationId'],'requestId':rid,'guardHash':digest(value['parentGuard']),'closureFenceId':value['closureFenceId'],'authorityHash':value['authorityHash'],'attempts':value['attempts'],'settledPublications':[],'status':'revoked'}
  if not isinstance(receipt,dict)or receipt!=live or any(receipt.get(k)!=v for k,v in expected.items())or receipt.get('schemaVersion')!='filex.publication-revocation.v1'or receipt.get('receiptHash')!=digest({k:v for k,v in receipt.items()if k!='receiptHash'}):raise ValueError('LOGICAL_REVOCATION_UNCONFIRMED')
  if any(receipt.get(k)is not False for k in ('processStopped','leaseReleased','cleanupCompleted')):raise ValueError('LOGICAL_REVOCATION_OVERCLAIM')
  with self.bridge.transaction():
   if self._closure(rid,actor)!=value:raise ValueError('LOGICAL_CLOSURE_CHANGED')
   prior=self.common.db.execute('SELECT receipt_json FROM resident_logical_closures WHERE parent_id=?',(rid,)).fetchone()[0]
   if prior and json.loads(prior)!=receipt:raise ValueError('LOGICAL_REVOCATION_CHANGED')
   self.common.db.execute('UPDATE resident_logical_closures SET receipt_json=? WHERE parent_id=?',(json.dumps(receipt,sort_keys=True),rid))
   return {'schemaVersion':'npc-logical-timeout-retry.v1','requestId':rid,'parentRevision':self.common.row(rid)['revision'],'logicalTimeout':True,'publicationFenced':True,'artifactsPreserved':True,'executionStopped':False,'ownershipReleased':False,'executionDisposition':'unknown-preserved','retryScope':'new-parent','revokedParentRequestId':rid,'scopeHash':value['authorityHash'],'fenceReceiptHash':receipt['receiptHash']}
