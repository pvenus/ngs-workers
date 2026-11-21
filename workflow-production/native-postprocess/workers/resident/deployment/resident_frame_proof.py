"""Service-side native scratch authority for issued resident frame attempts.

No prefix discovery, new admission, caller success flags, or legacy evidence
rewriting. Allocation is not completion: pre-stage and post-cleanup paths remain
exactly allocated by issued claims; release independently requires terminal and
original PDS publication evidence through the native producer factory.
"""
import hashlib,json,inspect
from pathlib import Path
from types import SimpleNamespace
from dataclasses import asdict
from ..contracts import Claim,PROFILES
from ..adapters.attempt_directory_lock import directory_lock,directory_read_cohort
from .request_selector import digest as payload_digest


def checked(value):
 p=Path(value)
 if not p.is_absolute() or p.resolve()!=p or any(n.is_symlink() for n in (p,*p.parents)):raise ValueError('RESIDENT_FRAME_PATH_INVALID')
 return p

def wire_claim(raw):
 value=json.loads(raw) if isinstance(raw,str) else dict(raw)
 value['input_refs']=tuple(value['input_refs']);c=Claim(**value);c.validate(PROFILES['postprocess']);return c

class ResidentFrameAllocations:
 def __init__(self,authority,legacy,admission_reader,*,scratch_root,root_key,managed_root,worker_id):
  self.authority,self.common,self.legacy,self.admission=authority,authority.common,legacy,admission_reader
  self.scratch,self.managed=checked(scratch_root),checked(managed_root)
  if self.managed not in self.scratch.parents or not self.scratch.is_dir() or not worker_id:raise ValueError('RESIDENT_FRAME_ROOT_BINDING')
  self.root_key,self.worker_id=root_key,worker_id
 def request(self,g):
  pinned=self.admission(g['requestId'])
  if any(pinned.get(k)!=g.get(k) for k in ('npcId','generationId','requestId','leaseToken')):raise ValueError('RESIDENT_FRAME_ADMISSION_CHANGED')
  row=self.common.row(g['requestId'])
  if row['request_type']!='npc.animation.frames.generate' or row['parent_id'] is not None or self.common.db.execute('SELECT 1 FROM common_requests WHERE parent_id=?',(g['requestId'],)).fetchone():raise ValueError('RESIDENT_FRAME_STANDALONE_REQUIRED')
  if self.common.db.execute("SELECT 1 FROM common_events WHERE request_id=? AND kind='legacy_import'",(g['requestId'],)).fetchone():raise ValueError('RESIDENT_FRAME_LEGACY_HISTORY_DENIED')
  issued={(int(v['attempt_id']),v['worker_id']) for v in self.common.db.execute('SELECT attempt_id,worker_id FROM resident_resource_grants WHERE request_id=?',(g['requestId'],))}
  if self.common.db.execute("SELECT 1 FROM sqlite_master WHERE name='resident_reconciliations'").fetchone():
   for v in self.common.db.execute('SELECT r.recovery_json FROM resident_reconciliations r JOIN resident_resource_grants g ON g.grant_id=r.grant_id WHERE g.request_id=?',(g['requestId'],)):
    if v[0]:
     recovery=json.loads(v[0]);issued.add((recovery['fence'],recovery['workerId']))
  if row['fence']>0 and not any(fence==row['fence'] for fence,_ in issued):raise ValueError('RESIDENT_FRAME_UNTRACKED_FENCE_DENIED')
  for event in self.common.db.execute("SELECT details_json FROM common_events WHERE request_id=? AND kind IN ('claimed','lease_recovered')",(g['requestId'],)):
   detail=json.loads(event[0])
   if (detail.get('fence'),detail.get('worker')) not in issued:raise ValueError('RESIDENT_FRAME_UNTRACKED_EXECUTION_DENIED')
  record=self.common.db.execute('SELECT * FROM frame_intake_requests WHERE id=?',(g['requestId'],)).fetchone()
  if record is None or record['actor']!=row['requester']:raise ValueError('RESIDENT_FRAME_ORIGINAL_OWNER')
  body=json.loads(record['body']);refs=self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(g['requestId'],)).fetchall()
  if body.get('npcId')!=g['npcId'] or payload_digest(body)!=record['input_hash'] or len([r for r in refs if r['reference_id']==g['requestId'] and r['input_hash']==record['input_hash'] and r['domain']=='frames.requests' and r['schema_version']=='ngs.frame-intake.v1'])!=1:raise ValueError('RESIDENT_FRAME_ORIGINAL_HASH')
  from .frame_operation_scope import frame_operation_scope
  frame_operation_scope(self.common,row,body)
  return row,body
 def claims(self,g):
  row,body=self.request(g);values=[]
  records=self.common.db.execute('SELECT * FROM resident_resource_grants WHERE request_id=? ORDER BY rowid',(g['requestId'],)).fetchall()
  for r in records:
   c=wire_claim(r['claim_json'])
   if (c.request_id!=g['requestId'] or c.capability!='frames.extract' or c.resource_grant!=r['grant_id'] or c.attempt_id!=r['attempt_id'] or dict(c.parameters)!=body or r['worker_id']!=self.worker_id or r['resource']!=PROFILES['postprocess'].resource):raise ValueError('RESIDENT_FRAME_ISSUED_CLAIM_CHANGED')
   binding=self.common.db.execute('SELECT binding_json FROM resident_claim_bindings WHERE grant_id=?',(c.resource_grant,)).fetchone()
   if binding is None:raise ValueError('RESIDENT_FRAME_BINDING_MISSING')
   b=json.loads(binding[0])
   if b.get('targetId')!=g['npcId'] or b.get('projectId')!=row['project_id'] or b.get('expectedRevision')!=c.expected_revision:raise ValueError('RESIDENT_FRAME_TARGET_CHANGED')
   folder=checked(self.scratch/payload_digest([c.request_id,c.attempt_id]));pin=folder/'claim.json'
   with directory_lock(folder) as exists:
    if exists and (not folder.is_dir() or not pin.is_file() or pin.is_symlink() or json.loads(pin.read_text())!=json.loads(json.dumps(asdict(c)))):raise ValueError('RESIDENT_FRAME_WORKSPACE_OWNER')
   values.append((c,folder,r))
  return values
 def entries(self,g):
  if not g['requestId'].startswith('frames.req.'):return self.legacy.entries(g)
  # Preserve the native deterministic legacy allocation as well. Its absence is
  # never treated as proof that an old producer completed or stopped.
  values=list(self.legacy.entries(g))
  if any(checked(v['path']).exists() for v in values):raise ValueError('RESIDENT_FRAME_LEGACY_WORKSPACE_DENIED')
  for c,path,row in self.claims(g):
   values.append({k:g[k]for k in ('npcId','generationId','requestId')}|{'allocated':True,'exclusive':True,'rootKey':self.root_key,'kind':'frame-job','path':str(path),'authorityType':'resident-issued-claim','authorityHash':payload_digest(asdict(c))})
  return values
 def __call__(self,g,kind,path):
  values=[v for v in self.entries(g) if v['kind']==kind and v['path']==path]
  if len(values)!=1:raise ValueError('RESIDENT_FRAME_EXACT_ALLOCATION_REQUIRED')
  return values[0]
 def verify_finish(self,g):
  if not g['requestId'].startswith('frames.req.'):return True
  row,_=self.request(g);claims=self.claims(g)
  if row['state']!='completed' or row['worker'] is not None or not claims:raise ValueError('RESIDENT_FRAME_NOT_COMPLETED')
  # Every issued attempt must be confirmed stopped, not merely the final one.
  for c,path,issued in claims:
   if self.authority.verify_stopped(c) is not True:raise ValueError('RESIDENT_FRAME_EXECUTION_UNCONFIRMED')
  current=[c for c,_,_ in claims if c.attempt_id==str(row['fence'])]
  # Reconciliation can complete under its own finalize fence. Its durable record
  # must explicitly bind the original source grant, not an inferred latest row.
  if not current:
   for c,_,_ in claims:
    recovery=self.common.db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id=?',(c.resource_grant,)).fetchone()
    if recovery and recovery[0] and json.loads(recovery[0]).get('fence')==row['fence']:current.append(c)
  if len(current)!=1:raise ValueError('RESIDENT_FRAME_FINAL_ATTEMPT_REQUIRED')
  c=current[0];checkpoint=self.common.db.execute('SELECT receipt_json,proof_json FROM resident_publication_checkpoints WHERE grant_id=?',(c.resource_grant,)).fetchone()
  if checkpoint is None:raise ValueError('RESIDENT_FRAME_PRE_CLEANUP_CHECKPOINT_REQUIRED')
  receipt=json.loads(checkpoint['receipt_json']);proof=json.loads(checkpoint['proof_json'])
  if any(receipt.get(k)!=v for k,v in {'request_id':c.request_id,'attempt_id':c.attempt_id,'fencing_token':c.fencing_token}.items()) or proof.get('sourceReceipt',{}).get('requestId')!=c.request_id or proof.get('publication',{}).get('verified') is not True:raise ValueError('RESIDENT_FRAME_CHECKPOINT_CHANGED')
  # Native factory.receipts performs the live PDS durable receipt check next.
  return True

class ResidentFrameRoutingProbe:
 REQUIRED={'workers/resident/adapters/frame_no_post.py','workers/resident/deployment/frame_no_post_site.py','workers/resident/adapters/pds_frames.py','workers/resident/adapters/pds_category_files.py','workers/resident/adapters/attempt_directory_lock.py','workers/resident/runtime.py','workers/resident/deployment/role_factory.py','workers/resident/deployment/resident_frame_proof.py','workers/resident/adapters/frame_workspace.py','workers/resident/adapters/frame_full_workspace.py','workers/resident/backends/frame_postprocess.py','workers/resident/backends/frame_full.py'}
 def __init__(self,legacy,allocations,*,source_root,source_pins,worker_config,workspace_for_claim,host_id,registry):
  self.legacy,self.allocations=legacy,allocations;self.source=checked(source_root);self.pins=dict(source_pins);self.config=worker_config;self.workspace=workspace_for_claim;self.host,self.registry=host_id,registry
  if not self.REQUIRED<=set(self.pins) or not callable(workspace_for_claim):raise ValueError('RESIDENT_FRAME_CODE_COVERAGE')
 def __call__(self,producer,g):
  if producer!='frames':return self.legacy(producer,g)
  from .composition import verify
  from ..adapters.frame_workspace import HalfFrameWorkspace
  from ..adapters.frame_full_workspace import FullFrameWorkspace
  from local.services.npc_scratch_registry import digest
  verify(self.config,'postprocess')
  settings=self.config['settings']
  if self.config.get('activationApproved') is not True or self.config['workerId']!=self.allocations.worker_id or settings.get('workerId')!=self.allocations.worker_id or checked(settings['scratchRoot'])!=self.allocations.scratch:raise ValueError('RESIDENT_FRAME_ACTIVE_CONFIG_CHANGED')
  for relative,sha in self.pins.items():
   path=checked(self.source/relative)
   if self.source not in path.parents or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('RESIDENT_FRAME_INSTALLED_CODE_CHANGED')
  _,body=self.allocations.request(g)
  hint=SimpleNamespace(request_id=g['requestId'],capability='frames.extract',parameters=body)
  actual=self.workspace(hint)
  if type(actual) not in (HalfFrameWorkspace,FullFrameWorkspace) or checked(actual.root)!=self.allocations.scratch or checked(inspect.getfile(type(actual)))!=self.source/('workers/resident/adapters/frame_workspace.py' if type(actual)is HalfFrameWorkspace else 'workers/resident/adapters/frame_full_workspace.py'):raise ValueError('RESIDENT_FRAME_INSTANTIATED_ROUTE_CHANGED')
  observed={'sourceRoot':str(self.source),'scratchRoot':str(actual.root),'kind':'frame-job','workerId':self.allocations.worker_id,'type':type(actual).__module__+'.'+type(actual).__name__}
  return {'verified':True,'hostId':self.host,'producer':producer,'producerRegistryHash':digest(self.registry),'allOutputHooks':True,'rootKeys':[self.allocations.root_key],'sourceManifestHash':digest(self.pins),'actualRoutesHash':digest(observed),'metadataOnly':False}

def bind_frame_inventory(factory,scratch):
 """Wrap only this native factory; never hold SH during normal cleanup."""
 from local.services.npc_scratch_registry import RegistryFactory
 if not isinstance(factory,RegistryFactory):raise ValueError('RESIDENT_FRAME_FACTORY_TYPE')
 class FrameCohortFactory(RegistryFactory):
  def __call__(self,guard):
   output=super().__call__(guard);inventory=output._inventory
   def locked_inventory():
    paths=[v[2]for v in output.workspaces.values()if v[2].parent==scratch]
    with directory_read_cohort(paths):return inventory()
   output._inventory=locked_inventory
   return output
 bound=FrameCohortFactory.__new__(FrameCohortFactory);bound.__dict__.update(factory.__dict__)
 return bound

def configure_resident_frames(service,request_id,*,config_path,authority,scratch_root,source_root,source_pins,worker_config,workspace_for_claim,call=None,factory_sink=None,install=True):
 """Replacement configure callback for native frame lifecycle only.
 Returns the actual native RegistryFactory; no runtime selector is changed.
 """
 from local.services.npc_scratch_bootstrap import private_json,install_packaged_host
 from local.services.npc_workflow_proofs import HostReaders
 from local.services.npc_scratch_allocations import MediaAllocations
 from local.services.npc_scratch_allocation_host import HostReports
 from local.services.npc_scratch_completion import completion_reader
 from local.services.npc_scratch_registry import digest
 c=private_json(config_path)
 if c.get('registry',{}).get('producers')!=['frames']:raise ValueError('RESIDENT_FRAME_SCOPED_REGISTRY_REQUIRED')
 readers=HostReaders(service.common,c['serviceRoots'])
 if readers.prefix(request_id)!='frames.req.' or checked(service.root)!=readers.roots['frames.req.'] or service.common is not authority.common:raise ValueError('RESIDENT_FRAME_SERVICE_BINDING')
 legacy=MediaAllocations(service.common,{p:readers.roots[p]for p in MediaAllocations.TABLES},c['mediaRootKeys'],readers.admission)
 key=c['mediaRootKeys']['frames.req.']
 allocations=ResidentFrameAllocations(authority,legacy,readers.admission,scratch_root=scratch_root,root_key=key,managed_root=c['roots'][key],worker_id=worker_config['workerId'])
 def prior(producer,g):raise ValueError('RESIDENT_FRAME_UNRELATED_PRODUCER_DENIED')
 probe=ResidentFrameRoutingProbe(prior,allocations,source_root=source_root,source_pins=source_pins,worker_config=worker_config,workspace_for_claim=workspace_for_claim,host_id=c['hostId'],registry=c['registry'])
 def legacy_evidence(g):
  directory=c.get('legacyEvidenceDirectory')
  if not directory:return {'complete':False,'unresolvedPaths':['unverified-legacy']}
  path=checked(directory)/(digest([g['npcId'],g['generationId']])+'.json')
  if not path.exists() and not path.is_symlink():return {'complete':False,'unresolvedPaths':['unverified-legacy']}
  return private_json(path)
 reports=HostReports(c['hostId'],c['registry'],c['roots'],allocations,probe,legacy_evidence)
 holders=[]
 def capture(factory):
  original=factory.receipts
  def receipts(g):allocations.verify_finish(g);return original(g)
  factory.receipts=receipts
  factory=bind_frame_inventory(factory,allocations.scratch)
  holders.append(factory)
  if factory_sink is not None:factory_sink(factory)
 install_packaged_host(config_path,common=service.common,admission_reader=readers.admission,publication_reader=readers.publication,allocation_reader=allocations,host_reports=reports,completion_reader=completion_reader(readers,call),call=call,factory_sink=capture,install=False)
 if install:
  from local.services.npc_scratch import configure
  configure(holders[0])
 return holders[0]
