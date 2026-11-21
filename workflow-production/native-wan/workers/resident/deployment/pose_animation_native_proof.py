"""Original media-parent ownership scope. Never borrows a OneStop lease.

Only the native animation/pose direction relationship may share its original
parent admission. Allocation and completion remain native RegistryFactory proofs.
"""
import json
from pathlib import Path
from .request_selector import digest

class MediaOperationScope:
 def __init__(self,common,admission_reader,*,family):
  if family not in ('pose','animation','image','document'):raise ValueError('MEDIA_OPERATION_FAMILY')
  self.common,self.admission,self.family=common,admission_reader,family
 def original(self,rid):
  family=self.family;row=self.common.row(rid)
  table='pose_intake_requests'if family=='pose'else'animation_intake_requests'
  schema='ngs.pose-intake.v1'if family=='pose'else'ngs.animation-intake.v1'
  domain='pose.requests'if family=='pose'else'animation.requests'
  record=self.common.db.execute('SELECT * FROM '+table+' WHERE id=?',(rid,)).fetchone()
  refs=self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
  if record is None or record['actor']!=row['requester'] or row['schema_version']!=schema:raise ValueError('MEDIA_OPERATION_ORIGINAL_OWNER')
  body=json.loads(record['body'])
  if digest(body)!=record['input_hash'] or len(refs)!=1 or any(refs[0][k]!=v for k,v in {'domain':domain,'schema_version':schema,'reference_id':rid,'input_hash':record['input_hash']}.items()):raise ValueError('MEDIA_OPERATION_ORIGINAL_HASH')
  return row,record,body
 def expected_ids(self,rid):
  row,_,body=self.original(rid)
  if self.family=='pose':
   from local.services.pose_requests.contract import directions
   return tuple(rid+'.'+d for d in directions(body))
  if row['request_type']=='npc.animation.generate-eight-directions':
   from local.services.animation_requests.contract import DIRECTIONS
   return tuple(rid+'.'+d for d in DIRECTIONS)
  return (rid,)
 def __call__(self,g):
  from .resident_operation_proof import OperationScope
  saved=self.admission(g['requestId'])
  if any(saved.get(k)!=g.get(k)for k in ('npcId','generationId','requestId','leaseToken')):raise ValueError('MEDIA_OPERATION_ADMISSION_CHANGED')
  parent,record,body=self.original(g['requestId'])
  if parent['parent_id']is not None or body.get('npcId')!=g['npcId']:raise ValueError('MEDIA_OPERATION_PARENT_SCOPE')
  family=self.family;rid=g['requestId'];children=[];binding={'requestId':rid,'inputHash':record['input_hash'],'family':family}
  if family=='animation':
   from local.services.animation_requests.contract import validate
   from local.services.animation_requests.batch import validate_batch
   if parent['request_type']=='npc.animation.generate-eight-directions':
    validate_batch(body)
    row=self.common.db.execute('SELECT state FROM animation_batch_state WHERE id=?',(rid,)).fetchone()
    state=json.loads(row[0])if row else None
    if state is not None and state.get('executionMode')!='external-children.v1':raise ValueError('MEDIA_OPERATION_INLINE_STATE')
    if state is not None:
     from local.services.animation_requests.contract import DIRECTIONS
     if [(s.get('direction'),s.get('requestId'))for s in state.get('steps',[])]!=[(d,rid+'.'+d)for d in DIRECTIONS]:raise ValueError('MEDIA_OPERATION_ANIMATION_STEPS')
     if 'seedMap'in state and set(state['seedMap'])!=set(DIRECTIONS):raise ValueError('MEDIA_OPERATION_ANIMATION_SEEDS')
    rows=self.common.db.execute('SELECT * FROM common_requests WHERE parent_id=?',(rid,)).fetchall()
    for child in rows:
     cr,_,cb=self.original(child['request_id']);direction=cb.get('direction');expected={**body,'direction':direction}
     if state and 'seedMap'in state:expected['seed']=state['seedMap'].get(direction)
     if not state or cb!=expected or cr['requester']!=parent['requester'] or cr['project_id']!=parent['project_id'] or cr['request_id']!=rid+'.'+str(direction) or not any(s.get('requestId')==cr['request_id'] and s.get('direction')==direction for s in state.get('steps',[])):raise ValueError('MEDIA_OPERATION_ANIMATION_CHILD')
     validate(cb);children.append(cr['request_id'])
    binding['state']=None if state is None else {k:state[k]for k in ('seedMap','referenceMap','selection','executionMode')if k in state}
   else:
    validate(body)
    if parent['request_type']not in ('npc.animation.generate','npc.animation.regenerate-one'):raise ValueError('MEDIA_OPERATION_ANIMATION_ROUTE')
    if self.common.db.execute('SELECT 1 FROM common_requests WHERE parent_id=?',(rid,)).fetchone():raise ValueError('MEDIA_OPERATION_UNEXPECTED_CHILD')
    children=[rid]
  else:
   from local.services.pose_requests.contract import validate,directions,routing
   from local.services.pose_requests.external import parent_state_hash
   validate(body)
   if parent['request_type']!=routing(body)[0]:raise ValueError('MEDIA_OPERATION_POSE_ROUTE')
   state=json.loads(record['state'])if record['state']else None
   if state is not None and (state.get('executionMode')!='external-directions.v1' or state.get('immutableStateHash')!=parent_state_hash(state) or state.get('requestHash')!=digest({**body,'requestId':rid})):raise ValueError('MEDIA_OPERATION_POSE_STATE')
   rows=self.common.db.execute('SELECT * FROM common_requests WHERE parent_id=?',(rid,)).fetchall()
   for child in rows:
    cid=child['request_id'];cr=self.common.db.execute('SELECT * FROM pose_direction_requests WHERE id=?',(cid,)).fetchone()
    refs=self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(cid,)).fetchall()
    if cr is None or state is None:raise ValueError('MEDIA_OPERATION_POSE_CHILD')
    cb=json.loads(cr['body']);direction=cb.get('direction')
    expected={'schemaVersion':'ngs.pose-direction-request.v1','parentRequestId':rid,'direction':direction,'parentInputHash':record['input_hash'],'parentStateHash':parent_state_hash(state)}
    if cb!=expected or cr['parent_id']!=rid or cr['actor']!=parent['requester'] or child['requester']!=parent['requester'] or child['project_id']!=parent['project_id'] or child['request_type']!='npc.pose.generate-direction' or child['schema_version']!='ngs.pose-direction-intake.v1' or direction not in directions(body) or cid!=rid+'.'+direction or not any(s.get('direction')==direction and s.get('requestId')==cid for s in state['steps']):raise ValueError('MEDIA_OPERATION_POSE_CHILD')
    if digest(cb)!=cr['input_hash'] or len(refs)!=1 or any(refs[0][k]!=v for k,v in {'domain':'pose.direction.requests','schema_version':'ngs.pose-direction-intake.v1','reference_id':cid,'input_hash':digest(cb)}.items()):raise ValueError('MEDIA_OPERATION_CHILD_HASH')
    children.append(cid)
   binding['parentStateHash']=parent_state_hash(state)if state else None
  return OperationScope(dict(g),tuple(sorted(children)),digest(binding))

class NativeMediaParentLifecycle:
 """Factory returns actual native RegistryFactory with independent proof readers."""
 def __init__(self,scope_reader,session_for_parent,factory_for_parent,*,failure_reader=None,released_session_for_parent=None):
  if failure_reader is not None and not callable(failure_reader):raise ValueError('MEDIA_FAILURE_READER_REQUIRED')
  self.scope,self.session,self.factory=scope_reader,session_for_parent,factory_for_parent
  if released_session_for_parent is not None and not callable(released_session_for_parent):raise ValueError('MEDIA_RELEASED_SESSION_READER_REQUIRED')
  self.released_session=released_session_for_parent
  self.failure_reader=failure_reader
 def ensure(self,rid,*,kind=None,path=None):
  from local.services.npc_scratch_registry import RegistryFactory
  session=self.session(rid)
  if not session.path.is_file()or session.path.is_symlink():raise ValueError('MEDIA_ORIGINAL_OWNERSHIP_PIN_REQUIRED')
  state=json.loads(session.path.read_text())
  if state.get('released')or state.get('requestId')!=rid or state.get('npcId')!=session.npc:raise ValueError('MEDIA_ORIGINAL_OWNERSHIP_CHANGED')
  if not state.get('guard'):session.enter();state=json.loads(session.path.read_text())
  guard=state['guard'];self.scope(guard)
  factory=self.factory(rid)
  if not isinstance(factory,RegistryFactory):raise ValueError('MEDIA_NATIVE_FACTORY_REQUIRED')
  output=factory(guard);output.start()
  if path is not None:output.register_workspace(kind,Path(path));output.verify_coverage()
  return session,state,output
 def finalize(self,rid):
  session=self.session(rid)
  if not session.path.is_file()or session.path.is_symlink():raise ValueError('MEDIA_ORIGINAL_OWNERSHIP_PIN_REQUIRED')
  state=json.loads(session.path.read_text())
  if state.get('requestId')!=rid or state.get('npcId')!=session.npc:raise ValueError('MEDIA_ORIGINAL_OWNERSHIP_CHANGED')
  if state.get('released'):return True
  common=self.scope.common;row=common.row(rid)
  if row['state']not in ('completed','blocked','failed','conflict') or row['worker']is not None:return False
  if not state.get('guard'):return False
  if row['state']!='completed':
   if self.failure_reader is None:return False
   # Trusted closure proof fences every child and proves stopped executions plus
   # settled publications. No discard, regeneration, cleanup or source rewrite.
   session,state,output=self.ensure(rid)
   result=output.finish_failed({'revision':row['revision']},proof_reader=self.failure_reader)
   if result.get('localLeaseReleased')is not True or result.get('globalLeaseReleased')is not False:raise ValueError('MEDIA_NATIVE_FAILURE_UNCONFIRMED')
   session.state=state;session.finish()
   return json.loads(session.path.read_text()).get('released')is True
  scoped=self.scope(state['guard'])
  if set(scoped.request_ids)!=set(self.scope.expected_ids(rid)):return False
  for child in scoped.request_ids:
   r=common.row(child)
   if r['state']!='completed'or r['worker']is not None:return False
  session,state,output=self.ensure(rid)
  result=output.finish_published(common.view(rid))
  if result.get('localLeaseReleased')is not True or result.get('globalLeaseReleased')is not False:raise ValueError('MEDIA_NATIVE_FINISH_UNCONFIRMED')
  session.state=state;session.finish()
  return json.loads(session.path.read_text()).get('released')is True

def bind_native_media_factory(original_factory,*,config,allocations,host_reports,admission_reader):
 """Compose a request-local native factory; never installs process-global state.

The original coordinator/receipt/terminal/cleanup readers are retained. Newly
issued scratch uses the native Issuer and RequestCoverage, not a success flag.
 """
 from local.services.npc_scratch_registry import RegistryFactory
 from local.services.npc_scratch_issuance import Issuer
 from local.services.npc_request_coverage import RequestCoverage
 from .resident_operation_proof import bind_operation_factory
 if not isinstance(original_factory,RegistryFactory):raise ValueError('MEDIA_NATIVE_FACTORY_REQUIRED')
 if config.get('registry')!=original_factory.registry or config['registry'].get('producers')!=[allocations.producer]:raise ValueError('MEDIA_NATIVE_REGISTRY_CHANGED')
 if {k:str(v)for k,v in original_factory.roots.items()}!=config.get('roots'):raise ValueError('MEDIA_NATIVE_ROOTS_CHANGED')
 issuer=Issuer(config,allocations,host_reports)
 coverage=RequestCoverage(issuer.coverage,admission_reader,config)
 factory=RegistryFactory(original_factory.coordinator,original_factory.roots,original_factory.registry,issuer.claim,coverage,original_factory.receipts,original_factory.terminal,original_factory.normal_cleanup,coverage.validate_execution,original_factory.failure_reader)
 return bind_operation_factory(factory,allocations)

def with_native_media_guard(domain,lifecycle,*,scratch_root,kind):
 """After domain proves exact parent membership, register only issued workspace."""
 if kind not in ('pose-request','animation-job'):raise ValueError('MEDIA_NATIVE_WORKSPACE_KIND')
 root=Path(scratch_root)
 if not root.is_absolute()or root.resolve()!=root:raise ValueError('MEDIA_NATIVE_SCRATCH_ROOT')
 original=domain['guard_resolver']
 def guard(claim):
  resolved=original(claim);g=resolved['guard'];rid=resolved['scopeRequestId']
  if rid!=g['requestId']or resolved['stageRequestId']!=claim.request_id:raise ValueError('MEDIA_NATIVE_SCOPE_CHANGED')
  path=root/digest([claim.request_id,claim.attempt_id])if hasattr(claim,'attempt_id')else None
  _,state,_=lifecycle.ensure(rid,kind=kind,path=path)
  if state['guard']!=g:raise ValueError('MEDIA_NATIVE_GUARD_CHANGED')
  return resolved
 return {**domain,'guard_resolver':guard}

class NativeMediaCompletionLoop:
 def __init__(self,common,lifecycle,admitted_request,family):
  from threading import Event
  self.common,self.lifecycle,self.admitted,self.family=common,lifecycle,admitted_request,family
  if family not in ('pose','animation','image','document'):raise ValueError('MEDIA_OPERATION_FAMILY')
  self.stop=Event();self.last_error=None
 def tick(self):
  types=('npc.pose.expand-eight-directions','npc.pose.regenerate-one')if self.family=='pose'else('npc.animation.generate-eight-directions','npc.animation.generate','npc.animation.regenerate-one')
  if self.family in ('image','document'):types=('npc.workflow',)
  result=[]
  for row in self.common.db.execute("SELECT * FROM common_requests WHERE parent_id IS NULL AND state IN ('completed','blocked','failed','conflict') ORDER BY created_at,request_id").fetchall():
   if row['request_type']not in types or self.admitted(dict(row))is not True:continue
   try:result.append((row['request_id'],'released'if self.lifecycle.finalize(row['request_id'])else'pending'))
   except Exception as exc:self.last_error=type(exc).__name__;result.append((row['request_id'],'pending'))
  return result
 def serve(self):
  while not self.stop.is_set():
   try:self.tick()
   except Exception as exc:self.last_error=type(exc).__name__
   self.stop.wait(1)

class MediaMetadataAllocations:
 """Allow only the original pose accept() binding, never legacy model outputs.

The generic allocator must be constructed without legacy. This wrapper adds the
native exact allocation, after independently matching its sole metadata file to
original pinned state; all issued attempt validation remains generic.
 """
 def __init__(self,issued,legacy,scope_reader):
  if issued.legacy is not None:raise ValueError('MEDIA_METADATA_LEGACY_DOUBLE_BINDING')
  self.issued,self.legacy,self.reader=issued,legacy,scope_reader
 def __getattr__(self,name):return getattr(self.issued,name)
 def entries(self,g):
  from .resident_frame_proof import checked
  self.reader(g);values=list(self.legacy.entries(g))
  for value in values:
   path=checked(value['path'])
   if not path.exists():continue
   if self.reader.family!='pose' or value['kind']!='pose-request' or not path.is_dir():raise ValueError('MEDIA_LEGACY_EXECUTION_PRESERVED')
   names=list(path.iterdir())
   if len(names)!=1 or names[0].name!='binding.json' or names[0].is_symlink()or not names[0].is_file():raise ValueError('MEDIA_LEGACY_EXECUTION_PRESERVED')
   _,record,_=self.reader.original(g['requestId']);state=json.loads(record['state']or'{}')
   expected={k:state[k]for k in ('context','selection','sourceAssetId','incarnationId','requestHash','targetSlotHash','rearReference')if k in state}
   if json.loads(names[0].read_text())!=expected or not expected.get('requestHash'):raise ValueError('MEDIA_POSE_METADATA_CHANGED')
   value.update(authorityType='original-pose-binding',authorityHash=digest(expected))
  values+=self.issued.entries(g)
  if len({(v['kind'],v['path'])for v in values})!=len(values):raise ValueError('MEDIA_DUPLICATE_WORKSPACE')
  return values
 def __call__(self,g,kind,path):
  rows=[v for v in self.entries(g)if(v['kind'],v['path'])==(kind,path)]
  if len(rows)!=1:raise ValueError('MEDIA_EXACT_WORKSPACE_REQUIRED')
  return rows[0]

class MediaParentLifecycleAdapter:
 """Existing animation_frame_site ensure(service,session) compatibility.

Admission happens through the original Session before its pinned guard reader;
this is not the post-resolution wrapper used for already-admitted requests.
 """
 def __init__(self,native):self.native=native
 def _check(self,service,session):
  expected=self.native.session(session.rid)
  if service.common is not self.native.scope.common or expected.path!=session.path or expected.npc!=session.npc or Path(service.root)!=session.path.parent.parent:raise ValueError('MEDIA_NATIVE_SERVICE_SESSION_CHANGED')
 def ensure(self,service,session,*,kind=None,path=None):
  self._check(service,session)
  _,state,_=self.native.ensure(session.rid,kind=kind,path=path)
  return state['guard']
 def finalize(self,service,session):
  self._check(service,session);return self.native.finalize(session.rid)
