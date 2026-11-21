"""In-process retry for an expired, never-published Request-owned portrait.

No new credential, HTTP port, native lease release or claimed process termination.
The original portrait service is the only publisher and reserves publication in
this same authority before any source mutation. An existing reservation blocks
this narrow path, even if its outcome is not known.
"""
import json
from .logical_retry import LogicalRetryAuthority
from .attempt_liveness import digest

class UnpublishedImageRetry(LogicalRetryAuthority):
 def __init__(self,bridge,*,parent_reader,record_reader):
  if not callable(record_reader):raise ValueError('IMAGE_ORIGINAL_RECORD_PORT_REQUIRED')
  super().__init__(bridge,parent_reader=parent_reader,revoke=lambda v:None,read_revocation=lambda v:None)
  self.record_reader=record_reader
 def read(self,stage,rid,actor,key=None):
  if stage not in ('portrait_representative','portrait_idle','portrait_combat'):return None
  try:return self.proof(rid,actor)
  except ValueError as exc:
   if str(exc)=='LOGICAL_TIMEOUT_PROOF_REQUIRED':return None
   raise
 def proof(self,rid,actor):
  from ..contracts import Claim
  with self.bridge.transaction():
   value=self._closure(rid,actor)
   if value['publicationState']!='never-started':raise ValueError('LOGICAL_PUBLICATION_RECONCILIATION_REQUIRED')
   # This is deliberately not a generic native guard revocation substitute.
   if len(value['attempts'])!=1 or len(value['requestIds'])!=2:raise ValueError('IMAGE_SINGLE_SCOPE_REQUIRED')
   attempt=value['attempts'][0];child=attempt['requestId']
   if child==rid or set(value['requestIds'])!={rid,child}:raise ValueError('IMAGE_SINGLE_SCOPE_REQUIRED')
   row=self.common.row(child)
   refs=self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(child,)).fetchall()
   if row['parent_id']!=rid or row['request_type']!='npc.workflow.stage' or row['schema_version']!='npc-workflow-portrait-single.v1' or len(refs)!=1 or refs[0]['domain']!='npc.single.portrait':raise ValueError('IMAGE_REQUEST_PUBLICATION_ONLY_REQUIRED')
   raw=self.common.db.execute('SELECT claim_json FROM resident_resource_grants WHERE grant_id=?',(attempt['grantId'],)).fetchone()[0]
   v=json.loads(raw);v['input_refs']=tuple(v['input_refs']);claim=Claim(**v)
   if claim.capability!='image.generate':raise ValueError('IMAGE_REQUEST_PUBLICATION_ONLY_REQUIRED')
   self.record_reader(claim) # actual original PairService record validation, read-only
   prior=self.common.db.execute('SELECT closure_json FROM resident_logical_closures WHERE parent_id=?',(rid,)).fetchone()
   if prior and json.loads(prior[0])!=value:raise ValueError('LOGICAL_CLOSURE_CHANGED')
   receipt={'schemaVersion':'request.unpublished-image-fence.v1','parentRequestId':rid,'scopeHash':value['authorityHash'],'publicationAuthority':'request-portrait-pair-service','publicationStarted':False,'executionStopped':False,'ownershipReleased':False}
   receipt['receiptHash']=digest(receipt)
   if not prior:self.common.db.execute('INSERT INTO resident_logical_closures VALUES(?,?,?)',(rid,json.dumps(value,sort_keys=True),json.dumps(receipt,sort_keys=True)))
   for member in value['requestIds']:
    old=self.common.db.execute('SELECT parent_id FROM resident_logical_fence_members WHERE request_id=?',(member,)).fetchone()
    if old and old[0]!=rid:raise ValueError('LOGICAL_CLOSURE_OVERLAP')
    self.common.db.execute('INSERT OR IGNORE INTO resident_logical_fence_members VALUES(?,?)',(member,rid))
   return {'schemaVersion':'npc-logical-timeout-retry.v1','requestId':rid,'parentRevision':self.common.row(rid)['revision'],'logicalTimeout':True,'publicationFenced':True,'artifactsPreserved':True,'executionStopped':False,'ownershipReleased':False,'executionDisposition':'unknown-preserved','retryScope':'new-parent','revokedParentRequestId':rid,'scopeHash':value['authorityHash'],'fenceReceiptHash':receipt['receiptHash']}
