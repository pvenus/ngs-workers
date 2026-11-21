"""Server-observed lease expiry: logical failure, never implicit physical release."""
from dataclasses import asdict
import json,hashlib
from threading import Event

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def timeout(common,claim):
 if not common.db.execute("SELECT 1 FROM sqlite_master WHERE name='resident_attempt_timeouts'").fetchone():return None
 r=common.db.execute('SELECT claim_json,proof_json FROM resident_attempt_timeouts WHERE grant_id=?',(claim.resource_grant,)).fetchone()
 if r is None:return None
 if r['claim_json']!=json.dumps(asdict(claim),sort_keys=True):raise ValueError('TIMEOUT_CLAIM_CHANGED')
 return json.loads(r['proof_json'])
def assert_live(common,claim):
 from .logical_retry import request_open
 request_open(common,claim.request_id)
 if timeout(common,claim)is not None:raise ValueError('ATTEMPT_KEEPALIVE_EXPIRED_FENCED')
 # An unknown terminal event is sticky; a replaying worker cannot resurrect it.
 if common.db.execute("SELECT 1 FROM common_events WHERE request_id=? AND event_key=?",(claim.request_id,'resident.execution-unknown.'+claim.attempt_id)).fetchone():raise ValueError('ATTEMPT_EXECUTION_UNKNOWN_FENCED')

class AttemptLiveness:
 def __init__(self,bridge,*,interval=5):
  if not 1<=interval<=30:raise ValueError('LIVENESS_INTERVAL')
  self.bridge,self.common=bridge,bridge.common;self.interval=interval;self.stop=Event();self.last_errors={}
 def initialize_schema(self):
  with self.bridge.transaction():self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_attempt_timeouts(grant_id TEXT PRIMARY KEY,request_id TEXT NOT NULL,claim_json TEXT NOT NULL,proof_json TEXT NOT NULL)')
 def expire(self,c):
  with self.bridge.transaction():
   old=timeout(self.common,c)
   if old:return old
   lease=self.bridge._issued_lease(c,allow_released=True);row=self.common.row(c.request_id)
   if row['state']not in ('processing','synchronizing')or row['lease_until'] is None or row['lease_until']>self.common.clock():return {'expired':False}
   if row['worker']!=lease['workerId']or row['nonce']!=lease['nonce']or row['fence']!=lease['fence']:raise ValueError('TIMEOUT_AUTHORITY_CHANGED')
   reservation=self.common.db.execute('SELECT state FROM resident_publications WHERE grant_id=?',(c.resource_grant,)).fetchone()
   checkpoint=self.common.db.execute('SELECT 1 FROM resident_publication_checkpoints WHERE grant_id=?',(c.resource_grant,)).fetchone()
   proof={'schemaVersion':'resident.attempt-timeout.v1','requestId':c.request_id,'grantId':c.resource_grant,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'scopeHash':digest(asdict(c)),'expired':True,'observedAt':self.common.clock(),'leaseUntil':row['lease_until'],'executionStopped':False,'resourceReleased':False,'ownershipReleased':False,'artifactsPreserved':True,'publicationState':'reconcile-required'if reservation or checkpoint else'revocation-required','retryAuthorized':False}
   proof['receiptHash']=digest(proof)
   self.common.db.execute('INSERT INTO resident_attempt_timeouts VALUES(?,?,?,?)',(c.resource_grant,c.request_id,json.dumps(asdict(c),sort_keys=True),json.dumps(proof,sort_keys=True)))
   self.common.block_expired(c.request_id,'resident.timeout.'+c.resource_grant,lease,'KEEPALIVE_TIMEOUT')
   self.common.event(c.request_id,'resident.timeout.proof.'+c.resource_grant,'keepalive_timeout','blocked','blocked',proof)
   return proof
 def tick(self):
  from ..contracts import Claim
  with self.bridge.transaction():rows=[r[0]for r in self.common.db.execute("SELECT g.claim_json FROM resident_resource_grants g JOIN common_requests r ON r.request_id=g.request_id WHERE g.released=0 AND r.state IN ('processing','synchronizing') AND r.lease_until<=?",(self.common.clock(),))]
  result=[]
  for raw in rows:
   v=json.loads(raw);v['input_refs']=tuple(v['input_refs']);c=Claim(**v)
   try:result.append(self.expire(c));self.last_errors.pop(c.resource_grant,None)
   except ValueError as exc:self.last_errors[c.resource_grant]=str(exc)
  return result
 def serve(self):
  import logging
  while not self.stop.wait(self.interval):
   try:self.tick()
   except Exception as exc:logging.getLogger(__name__).error('ATTEMPT_LIVENESS_TICK_FAILED %s',type(exc).__name__)
