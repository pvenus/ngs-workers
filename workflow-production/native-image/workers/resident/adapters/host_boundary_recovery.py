"""Inactive service-only recovery for an observed *new boot*, never a PID guess.

No HTTP, reboot, process kill, output adoption, or production binding. Trusted
observation/approval readers must be installed and independently reviewed.
"""
from dataclasses import dataclass,asdict
import hashlib,json,time
from pathlib import Path
from ..contracts import PROFILES

def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def require(ok,code):
 if not ok:raise ValueError(code)
def assert_open(common,rid):
 if common.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_host_boundaries'").fetchone()and common.db.execute('SELECT 1 FROM resident_host_boundaries WHERE request_id=?',(rid,)).fetchone():raise ValueError('HOST_RECOVERY_ATTEMPT_FENCED')
@dataclass(frozen=True)
class HostObservation:
 host_id:str
 boot_id:str
 boot_started_at:float
 observed_at:float
 worker_id:str
 stopped:bool
 restart_disabled:bool

class HostBoundaryRecovery:
 def __init__(self,bridge,*,host_id,worker_id,pins,observe,approval,clock=time.time):
  require(host_id and worker_id and pins and callable(observe)and callable(approval),'HOST_RECOVERY_CONFIG')
  self.bridge,self.common=bridge,bridge.common;self.host_id,self.worker_id=host_id,worker_id;self.pins=tuple(pins);self.observe,self.approval,self.clock=observe,approval,clock
 def initialize_schema(self):
  with self.bridge.transaction():self.common.db.execute('CREATE TABLE IF NOT EXISTS resident_host_boundaries(request_id TEXT PRIMARY KEY,grant_id TEXT UNIQUE NOT NULL,record_json TEXT NOT NULL)')
 def _pins(self):
  for pin in self.pins:
   p=Path(pin.path);require(p.is_absolute()and not p.is_symlink()and p.is_file()and hashlib.sha256(p.read_bytes()).hexdigest()==pin.sha256,'HOST_RECOVERY_SOURCE_CHANGED')
  return digest([asdict(p)for p in self.pins])
 def _observation(self,stopped):
  o=self.observe();now=self.clock()
  require(isinstance(o,HostObservation)and o.host_id==self.host_id and o.worker_id==self.worker_id and isinstance(o.boot_id,str)and o.boot_id and type(o.boot_started_at)in(int,float)and type(o.observed_at)in(int,float)and o.boot_started_at<=o.observed_at and 0<=now-o.observed_at<=5,'HOST_RECOVERY_OBSERVATION_UNKNOWN')
  if stopped:require(o.stopped is True and o.restart_disabled is True,'HOST_RECOVERY_WORKER_NOT_QUIESCED')
  return o
 def _snapshot(self,c):
  c.validate(PROFILES['document']);require(c.capability=='document.edit','HOST_RECOVERY_ROLE')
  db=self.common.db;g=db.execute('SELECT * FROM resident_resource_grants WHERE grant_id=?',(c.resource_grant,)).fetchone()
  require(g is not None and g['claim_json']==json.dumps(asdict(c),sort_keys=True)and g['worker_id']==self.worker_id,'HOST_RECOVERY_CLAIM_CHANGED')
  row=self.common.row(c.request_id);require(row['request_type']=='npc.workflow'and row['schema_version']=='ngs.npc-category-candidate.v1'and str(row['fence'])==c.fencing_token and row['state']in('processing','synchronizing','blocked','failed'),'HOST_RECOVERY_REQUEST_CHANGED')
  require(not row['source_receipt']and not row['publication_receipt'],'HOST_RECOVERY_PUBLICATION_EXISTS')
  for table,col,key in [('resident_failure_tracking','request_id',c.request_id),('resident_failure_grants','grant_id',c.resource_grant)]:
   t=db.execute('SELECT publication_started FROM '+table+' WHERE '+col+'=?',(key,)).fetchone();require(t is not None and t[0]==0,'HOST_RECOVERY_PUBLICATION_UNKNOWN')
  for table in ('resident_publications','resident_publication_checkpoints'):
   require(db.execute('SELECT 1 FROM '+table+' WHERE grant_id=?',(c.resource_grant,)).fetchone()is None,'HOST_RECOVERY_PUBLICATION_RESERVED')
  if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resident_model_leases'").fetchone():require(db.execute('SELECT 1 FROM resident_model_leases WHERE worker_id=? AND released=0',(self.worker_id,)).fetchone()is None,'HOST_RECOVERY_MODEL_UNPROVEN')
  refs=[dict(v)for v in db.execute('SELECT * FROM common_payload_refs WHERE request_id=? ORDER BY domain,schema_version,reference_id',(c.request_id,))]
  require(len(refs)==1 and refs[0]['domain']=='npc.category-candidate'and refs[0]['input_hash']==digest(c.parameters),'HOST_RECOVERY_REFERENCE_CHANGED')
  binding=db.execute('SELECT binding_json FROM resident_claim_bindings WHERE grant_id=?',(c.resource_grant,)).fetchone();require(binding is not None,'HOST_RECOVERY_BINDING_MISSING')
  return {'claim':asdict(c),'workerId':g['worker_id'],'lease':json.loads(g['lease_json']),'binding':json.loads(binding[0]),'refs':refs,'requester':row['requester'],'parentId':row['parent_id']}
 def _read(self,c):
  row=self.common.db.execute('SELECT record_json FROM resident_host_boundaries WHERE request_id=? AND grant_id=?',(c.request_id,c.resource_grant)).fetchone();require(row is not None,'HOST_RECOVERY_NOT_ARMED');return json.loads(row[0])
 def arm(self,c):
  pins=self._pins();o=self._observation(True)
  with self.bridge.transaction():snap=self._snapshot(c)
  approval=self.approval(c)
  require(isinstance(approval,dict)and approval.get('action')=='discard-unpublished-after-host-boundary'and approval.get('scopeHash')==digest(asdict(c))and approval.get('requestId')==c.request_id and approval.get('approved')is True and isinstance(approval.get('approvalId'),str)and approval['approvalId'],'HOST_RECOVERY_APPROVAL_REQUIRED')
  with self.bridge.transaction():
   require(self._snapshot(c)==snap,'HOST_RECOVERY_SCOPE_CHANGED')
   old=self.common.db.execute('SELECT record_json FROM resident_host_boundaries WHERE request_id=?',(c.request_id,)).fetchone()
   if old:
    r=json.loads(old[0]);require(r['snapshotHash']==digest(snap)and r['scopeHash']==digest(asdict(c)),'HOST_RECOVERY_SCOPE_CHANGED');return r
   r={'schemaVersion':'resident.host-boot-boundary.v1','requestId':c.request_id,'grantId':c.resource_grant,'scopeHash':digest(asdict(c)),'snapshotHash':digest(snap),'hostId':self.host_id,'workerId':self.worker_id,'before':asdict(o),'pinsHash':pins,'approvalId':approval['approvalId'],'state':'armed'}
   self.common.db.execute('INSERT INTO resident_host_boundaries VALUES(?,?,?)',(c.request_id,c.resource_grant,json.dumps(r,sort_keys=True)))
   self.common.event(c.request_id,'host-recovery.arm.'+c.resource_grant,'host_recovery_fenced',self.common.row(c.request_id)['state'],self.common.row(c.request_id)['state'],{'scopeHash':r['scopeHash'],'publicationRevoked':True,'executionStopped':False})
   return r
 def confirm(self,c):
  pins=self._pins();o=self._observation(True)
  with self.bridge.transaction():
   r=self._read(c);require(r['pinsHash']==pins and r['snapshotHash']==digest(self._snapshot(c)),'HOST_RECOVERY_SCOPE_CHANGED')
   before=r['before'];require(o.host_id==before['host_id']and o.boot_id!=before['boot_id']and o.boot_started_at>before['observed_at'],'HOST_RECOVERY_NEW_BOOT_REQUIRED')
   if r['state']=='verified':require(r['after']['boot_id']==o.boot_id,'HOST_RECOVERY_BOOT_CHANGED');return r
   require(r['state']=='armed','HOST_RECOVERY_STATE');r.update(state='verified',after=asdict(o),evidenceKind='host-boot-boundary',executionStopped=True)
   self.common.db.execute('UPDATE resident_host_boundaries SET record_json=? WHERE request_id=?',(json.dumps(r,sort_keys=True),c.request_id));return r
 def verify_stopped(self,c):
  try:
   pins=self._pins();o=self._observation(False)
   with self.bridge.transaction():
    r=self._read(c);return r['state']=='verified'and r['snapshotHash']==digest(self._snapshot(c))and r['pinsHash']==pins and r['after']['boot_id']==o.boot_id and r['hostId']==o.host_id
  except Exception:return False
 def acknowledge_finalized(self,c):
  # Called under the claim journal transaction. This never returns a runnable claim.
  try:
   r=self._read(c);g=self.common.db.execute('SELECT released FROM resident_resource_grants WHERE grant_id=?',(c.resource_grant,)).fetchone()
   return (r['state']=='verified'and r.get('finalized') is True and r.get('finalizedScopeHash')==digest(asdict(c))
    and r['scopeHash']==digest(asdict(c))and r['snapshotHash']==digest(self._snapshot(c))
    and r['pinsHash']==self._pins()and r['after']['boot_id']!=r['before']['boot_id']
    and r['after']['boot_started_at']>r['before']['observed_at']
    and g is not None and g['released']==1 and self.common.row(c.request_id)['state']in('blocked','failed'))
  except Exception:return False
 def finalize_failed(self,c):
  require(self.verify_stopped(c),'HOST_RECOVERY_STOP_UNCONFIRMED')
  with self.bridge.transaction():
   r=self._read(c);snap=self._snapshot(c);require(r['snapshotHash']==digest(snap),'HOST_RECOVERY_SCOPE_CHANGED');row=self.common.row(c.request_id)
   # Service-owned official state transition; never a fabricated joined report.
   if row['state']in('processing','synchronizing'):
    if row['lease_until']<=self.common.clock():self.common.block_expired(c.request_id,'host-recovery.fail.'+c.resource_grant,snap['lease'],'HOST_BOUNDARY_ABORTED_UNPUBLISHED')
    else:self.common.transition(c.request_id,'host-recovery.fail.'+c.resource_grant,'blocked',lease=snap['lease'],error='HOST_BOUNDARY_ABORTED_UNPUBLISHED',details={'artifactsPreserved':True})
   self.common.db.execute('UPDATE resident_resource_grants SET released=1 WHERE grant_id=?',(c.resource_grant,))
   r.update(finalized=True,finalizedScopeHash=digest(asdict(c)))
   self.common.db.execute('UPDATE resident_host_boundaries SET record_json=? WHERE request_id=?',(json.dumps(r,sort_keys=True),c.request_id))
   self.common.event(c.request_id,'host-recovery.finished.'+c.resource_grant,'host_recovery_failed',self.common.row(c.request_id)['state'],self.common.row(c.request_id)['state'],{'evidenceKind':'host-boot-boundary','artifactsPreserved':True,'executionStopped':True,'sourceApplied':False})
   return {'requestId':c.request_id,'state':self.common.row(c.request_id)['state'],'resourceReleased':True,'artifactsPreserved':True,'evidenceKind':'host-boot-boundary'}
