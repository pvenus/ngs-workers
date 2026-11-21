"""Request-side native lane on the existing pinned Common/Session/PDS authority.

Site supplies installed selected roots, original Coordinator and source pins.
Construction is inert: no schema writes, admission, service installation or
model execution until the existing startup/intake/controller invokes its port.
"""
from dataclasses import asdict
import json
from pathlib import Path
from threading import RLock
from urllib.parse import urlencode
import importlib

from ..adapters.attempt_directory_lock import directory_read_cohort,directory_lock,checked
from ..adapters.frame_workspace import HalfFrameWorkspace,digest
from ..adapters.native_png_inputs import freeze_inputs, verify_frozen_for_selection
from ..adapters.native_png_workspace import RAW,ALPHA,PIXEL
from ..contracts import Claim
from .native_png_failure import bind_native_failure,attach_native_failure_reader
from .native_png_host_observer import NativePNGHostObserver
from .native_png_publication import NativePNGPublicationAuthority
from .native_png_readback import NativePNGReadback
from .native_png_service import bind_native_service
from .native_png_storage import NativePNGStorage
from .workflow_gateway_site import gateway_from_pinned_sources

PHASE={RAW:'raw',ALPHA:'alpha',PIXEL:'pixel'}
MODULES={
 'workflow':'local.services.animation_requests.native_png_workflow',
 'host':'local.services.animation_requests.native_png_host',
 'formal':'local.services.npc_one_stop.native_png',
 'bootstrap':'local.services.npc_scratch_bootstrap',
 'registry':'local.services.npc_scratch_registry',
 'close':'local.services.npc_one_stop.close_failed',
}

def require(ok,code):
 if not ok:raise ValueError('NATIVE_REQUEST_'+code)

def _modules(sources):
 anchor=Path(sources.modules['common'].__file__).resolve().parents[3]
 result={key:importlib.import_module(name)for key,name in MODULES.items()}
 require(all(Path(module.__file__).resolve().is_relative_to(anchor)for module in result.values()),'NGS_PIN')
 return result

def _existing_directory(value):
 p=Path(value)
 require(p.is_absolute() and p.resolve(strict=True)==p and p.is_dir(),'ROOT')
 return p

class NativePNGRequestComposition:
 def __init__(self,sources,bridge,settings,*,pds_url,coordinator,ownership_root,roots,registry,routes,source_pins,
              call=None,reader_factory=NativePNGReadback):
  require(settings.get('nativePNG') is True and callable(reader_factory),'SETTINGS')
  if call is None:
   require(pds_url.rstrip('/')=='http://127.0.0.1:8030','OWNERSHIP_ENDPOINT')
   call=sources.modules['ownership'].api
  require(callable(call),'OWNERSHIP_PORT')
  self.sources,self.bridge,self.common=sources,bridge,bridge.common
  self.pds_url,self.call,self.reader_factory=pds_url,call,reader_factory
  self.modules=m=_modules(sources)
  self.coordinator,self.roots,self.registry,self.routes=coordinator,dict(roots),registry,dict(routes)
  self.owner_root=_existing_directory(ownership_root)
  self.workflow=m['workflow'].NativePNGWorkflow(self.common,read_raw=self.read_raw,read_alpha=self.read_alpha)
  self.observer=NativePNGHostObserver(bridge,routes=routes,producer_registry_hash=m['registry'].digest(registry),
       source_pins=source_pins,verify_cleaned=self.cleaned,enabled=True)

 def generation(self,npc):
  value=self.call('GET','/v1/npcs/ownership/generation?'+urlencode({'npcId':npc}))
  require(value.get('npcId')==npc and value.get('state')=='active' and bool(value.get('generationId')),'GENERATION')
  return {k:value[k]for k in ('npcId','generationId','state')}

 def readback(self,phase,task):
  require(not self.common.db.in_transaction,'READ_TRANSACTION')
  return self.reader(task['requestId'],task['parentRequestId']).readback(phase,task)

 def read_raw(self,task):
  value=self.readback('raw',task)
  return {k:value[k]for k in ('manifest','receipt')}if value is not None else None

 def read_alpha(self,task):return self.readback('alpha',task)

 def _session(self,npc,parent):
  return self.sources.session_type(self.owner_root,npc,parent,self.call)

 def _pin(self,parent,actor=None):
  plan=self.workflow.select(parent,'batch');scope=plan['scope']
  row=self.common.row(parent)
  require(actor is None or row['requester']==actor,'OWNER')
  session=self._session(scope['npcId'],parent)
  pin=self.modules['bootstrap'].private_json(session.path);g=pin.get('guard')
  exact={'npcId':scope['npcId'],'generationId':scope['generationId'],'requestId':parent}
  require(type(g)is dict and set(g)==set(exact)|{'leaseToken'}
          and all(g[k]==v and pin.get(k)==v for k,v in exact.items())
          and isinstance(g['leaseToken'],str)and bool(g['leaseToken']),'ORIGINAL_GUARD')
  return session,pin

 def reader(self,stage,parent):
  _,pin=self._pin(parent)
  return self.reader_factory(self.pds_url,lambda:dict(pin['guard']),stage)

 def prepare(self,src,actor,parent,incarnation):
  require(not self.common.db.in_transaction,'READ_TRANSACTION')
  npc=src['npcId'];g=self.generation(npc);session=self._session(npc,parent)
  # The original incarnation was validated by the formal adapter; repeat it
  # before admission using its ordinary selected PDS read authority.
  current=self.call('GET','/api/npcs');matches=[n for n in current['document']['characters']if n['id']==npc]
  require(current.get('authority')=='filex' and len(matches)==1
     and matches[0].get('creationBasis',{}).get('requestId')==incarnation,'INCARNATION')
  session.pin(g['generationId']);guard=session.enter()
  reader=self.reader_factory(self.pds_url,lambda:dict(guard),parent)
  return session,g['generationId'],lambda request,generation_id:freeze_inputs(
       reader,request,generation_id=generation_id,read_generation=self.generation)[0]

 def verify_frozen(self,request,frozen):
  # Re-read the exact candidate solely to recover its already persisted native
  # parent/stage. Ambiguous/concurrent selection is not a semantic input failure.
  selected=self.workflow._resident_candidate('animation',frozen=True)
  if selected is None:return False
  _,task,_=selected
  if task['directionRequest']!=request or task['frozenInputs']!=frozen:return False
  return verify_frozen_for_selection(self.reader(task['requestId'],task['parentRequestId']),
                                    request,frozen,read_generation=self.generation)

 def _bound(self,claim):
  phase=PHASE.get(claim.parameters.get('schemaVersion'))
  require(phase is not None and self.workflow.select(claim.request_id,phase)==claim.parameters,'CLAIM')
  return phase,claim.parameters

 def guard(self,claim):
  _,task=self._bound(claim)
  _,pin=self._pin(task['parentRequestId'])
  require(pin.get('released') is not True and pin['generationId']==task['generationId'],'ORIGINAL_GUARD')
  return {'guard':pin['guard'],'scopeRequestId':task['parentRequestId'],'stageRequestId':claim.request_id}

 def proof(self,claim,receipt):
  phase,task=self._bound(claim)
  return self.workflow.completion_proof(phase,claim.request_id,receipt_asset_ids=receipt.asset_refs,
        receipt_revision=receipt.revision,readback=self.readback)

 def workspace(self,claim):
  phase,_=self._bound(claim)
  return HalfFrameWorkspace(self.routes['animation' if phase=='raw'else'postprocess']['scratchRoot'],None)

 def register_workspace(self,claim,guard):
  """Register only the caller's already staged attempt; peers are untouched."""
  phase,task=self._bound(claim);parent=task['parentRequestId']
  _,pin=self._pin(parent)
  require(pin.get('released') is not True and pin['guard']==guard,'ORIGINAL_GUARD')
  plan=self.workflow.select(parent,'batch')
  require(plan.get('requireHostReady') is True and self.workflow._host_ready(plan),'WORKSPACE_HOST')
  self.observer._check()
  expected_ready={'parentRequestId':parent,'npcId':guard['npcId'],'generationId':guard['generationId'],
      'planHash':digest(plan),'guardHash':digest(guard),'registryHash':self.observer.registry}
  require(json.loads(self.common.db.execute('SELECT proof FROM npc_native_png_host_ready WHERE parent_id=?',(parent,)).fetchone()[0])==expected_ready,'WORKSPACE_HOST')
  role='animation' if phase=='raw' else 'postprocess'
  route=self.routes[role];root=_existing_directory(self.roots[route['rootKey']])
  folder=self.workspace(claim)._folder(claim)
  require(root in folder.parents,'WORKSPACE_SCOPE')
  with directory_lock(folder)as exists:
   require(exists,'WORKSPACE_NOT_STAGED')
   def load(name):
    path=checked(folder/name)
    require(path.is_file(),'WORKSPACE_MARKER')
    return json.loads(path.read_text())
   exact=json.loads(json.dumps(asdict(claim)))
   require(load('claim.json')==exact,'WORKSPACE_OWNER')
   if phase=='raw':
    staged=load('animation-job.json')
    expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,
       'fencingToken':claim.fencing_token,'expectedRevision':claim.expected_revision,
       'parametersHash':digest(task),'inputRefs':list(claim.input_refs)}
    require(staged.get('claim')==expected,'WORKSPACE_MARKER')
   else:require(load('native-stage.json').get('claim')==exact,'WORKSPACE_MARKER')
   inventory=[]
   # Keep the original Common claim CAS held across local registration only.
   # No PDS/network operation occurs inside this transaction.
   with self.bridge.transaction():
    self.bridge.authorize_publication(claim)
    _,current=self._pin(parent)
    require(current==pin and self.workflow.select(parent,'batch')==plan
        and self.workflow._host_ready(plan) and json.loads(self.common.db.execute(
            'SELECT proof FROM npc_native_png_host_ready WHERE parent_id=?',(parent,)).fetchone()[0])==expected_ready,
        'WORKSPACE_HOST_CHANGED')
    names=[]
    for member in sorted(folder.rglob('*')):
     path=checked(member)
     if path.is_dir():continue
     require(path.is_file(),'WORKSPACE_NONREGULAR')
     names.append(str(path.relative_to(root)))
    kind='animation-scratch' if phase=='raw' else 'frame-scratch'
    for relative in names:
     sha=self.coordinator.register(guard['npcId'],guard['generationId'],parent,route['rootKey'],relative,kind)
     inventory.append([relative,sha])
    self.bridge.authorize_publication(claim)
   return {'registered':True,'requestId':claim.request_id,'attemptId':claim.attempt_id,
           'fencingToken':claim.fencing_token,'inventoryHash':digest(inventory)}

 def recovery(self,claim):
  from .native_png_recovery import select_native_recovery
  _,task=self._bound(claim)
  return select_native_recovery({'nativePNG':True},dict(authority=self.bridge,workflow=self.workflow,
   workspace=self.workspace(claim),readback=self.readback,
   read_asset=self.reader(claim.request_id,task['parentRequestId']).asset))

 def terminal(self,dto):
  claim=Claim(**{**dto,'input_refs':tuple(dto['input_refs'])})
  return self.bridge.verify_stopped(claim) is True

 def cleaned(self,claim):
  dto=asdict(claim)
  if self.workflow.verify_attempt_cleanup(dto):return True
  return self.host_for(claim.parameters['parentRequestId'],None).verify_failed_attempt_cleanup(dto)

 def host_for(self,parent,actor):
  session,_=self._pin(parent,actor)
  host=self.modules['host'].NativePNGHostBinding(self.workflow,session=session,coordinator=self.coordinator,
    roots=self.roots,registry=self.registry,routes=self.routes,observe_role=self.observer,verify_terminal=self.terminal,
    readback=self.readback,call=self.call,inventory_cohort=directory_read_cohort,enabled=True)
  # The sole existing fence is built after domain_factory returns.
  if self.bridge.failure_fence is not None:attach_native_failure_reader(host,self.bridge)
  return host

 def adapter(self,root):
  return self.modules['formal'].NativeFormalAdapters(root,read=lambda:self.call('GET','/api/npcs'),
    native_png=True,workflow=self.workflow,prepare=self.prepare,host_for=self.host_for)

 def owner_close_failed(self,service,rid,actor):
  require(service.common is self.common and isinstance(service.adapter,self.modules['formal'].NativeFormalAdapters)
          and service.adapter.workflow is self.workflow,'OWNER_SERVICE')
  return self.modules['close'].close_failed(service,rid,actor,call=self.call)

 def bind(self,domain,*,one_stop_root,executor_id,owns_parent,parent_loop=None):
  value=bind_native_service(self.bridge,domain,{'nativePNG':True},workflow_factory=lambda:self.workflow,
     verify_frozen=self.verify_frozen,proof=self.proof,recovery_reader=self.recovery,
     workspace=self.workspace,guard_resolver=self.guard,parent_loop=parent_loop)
  value=bind_native_failure(self.bridge,value,self.host_for,enabled=True)
  self.bridge.native_workspace_registrar=self.register_workspace
  original=value['initialize_schema']
  def initialize_schema():
   original();self.workflow.initialize()
  value['initialize_schema']=initialize_schema
  value['native_formal_adapter_factory']=self.adapter
  value['native_owner_close_failed']=self.owner_close_failed
  value['workflow_gateway']=gateway_from_pinned_sources(self.sources,self.common,RLock(),
       one_stop_root=one_stop_root,executor_id=executor_id,owns_parent=owns_parent,formal_adapter_factory=self.adapter)
  def never_direct(*_):raise ValueError('NATIVE_REQUEST_PDS_DATA_PLANE_ONLY')
  value['owned_operation_authority']=NativePNGPublicationAuthority(NativePNGStorage(self.bridge,never_direct,enabled=True))
  return value

def compose_native_png_request(sources,bridge,domain,settings,*,one_stop_root=None,
                               executor_id=None,owns_parent=None,parent_loop=None,**bindings):
 enabled=settings.get('nativePNG',False)
 require(type(enabled)is bool,'OPT_IN')
 if not enabled:return domain
 require(callable(owns_parent)and bool(executor_id),'GATEWAY')
 composed=NativePNGRequestComposition(sources,bridge,settings,**bindings)
 return composed.bind(domain,one_stop_root=one_stop_root,executor_id=executor_id,owns_parent=owns_parent,
                      parent_loop=parent_loop)
