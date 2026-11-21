"""Native SingleBatch ownership, using only its original admission and children.

No OneStop guard sharing, no implicit admission, no new publication authority.
The caller supplies the existing original record validator, not request booleans.
"""
import json
from pathlib import Path
from .request_selector import digest
from .resident_operation_proof import OperationScope
from .pose_animation_native_proof import NativeMediaParentLifecycle, bind_native_media_factory

class SingleBatchOperationScope:
 def __init__(self,common,admission_reader,record_reader,*,family):
  if family not in ('document','image'):raise ValueError('SINGLE_NATIVE_FAMILY')
  self.common,self.admission,self.record,self.family=common,admission_reader,record_reader,family
 def original(self,rid):
  from local.services.npc_lifecycle.workflow_contract import plan,execution_invocations,execution_stage,worker_count_contract
  row=self.common.row(rid)
  native=self.common.db.execute('SELECT * FROM npc_single_batches WHERE id=?',(rid,)).fetchone()
  if native is None or row['parent_id']is not None or row['request_type']!='npc.workflow' or row['schema_version']!='ngs.npc-single-batch.v1' or native['actor']!=row['requester']:raise ValueError('SINGLE_NATIVE_PARENT')
  request=json.loads(native['request_json']);selection=json.loads(native['selection_json'])
  plan('single',selection);stage=execution_stage(selection);calls=execution_invocations(selection)
  allowed=('portrait',)if self.family=='image'else('overview','art','category-add','category-modify')
  if digest(request)!=native['input_hash'] or selection.get('requestHash')!=native['input_hash'] or not worker_count_contract(selection) or len(calls)!=1 or stage.get('candidateCount')!=1 or stage.get('id')not in allowed:raise ValueError('SINGLE_NATIVE_SELECTION')
  refs=self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
  if len(refs)!=1 or any(refs[0][k]!=v for k,v in {'domain':'npc.single-batch','schema_version':'ngs.npc-single-batch.v1','reference_id':rid,'input_hash':digest(request)}.items()):raise ValueError('SINGLE_NATIVE_PARENT_HASH')
  return row,request,selection,stage,calls
 def expected_ids(self,rid):
  _,_,_,_,calls=self.original(rid)
  rows=self.common.db.execute('SELECT * FROM npc_single_invocations WHERE parent_id=?',(rid,)).fetchall()
  if len(rows)!=len(calls) or {r['invocation_id']for r in rows}!={c['id']for c in calls}:raise ValueError('SINGLE_NATIVE_INVOCATIONS')
  return tuple(r['child_id']or ('unregistered:'+r['invocation_id']) for r in rows)
 def __call__(self,g):
  rid=g['requestId'];saved=self.admission(rid)
  if any(saved.get(k)!=g.get(k)for k in ('npcId','generationId','requestId','leaseToken')):raise ValueError('SINGLE_NATIVE_ADMISSION')
  parent,request,selection,stage,calls=self.original(rid)
  if (request.get('npcId')or request.get('characterRef',{}).get('npcId'))!=g['npcId']:raise ValueError('SINGLE_NATIVE_NPC')
  if self.common.db.execute('SELECT 1 FROM npc_single_duplicate_retries WHERE parent_id=?',(rid,)).fetchone():raise ValueError('SINGLE_NATIVE_RETRY_UNSUPPORTED')
  expected=self.expected_ids(rid);registered={x for x in expected if not x.startswith('unregistered:')}
  children=self.common.db.execute('SELECT * FROM common_requests WHERE parent_id=?',(rid,)).fetchall()
  if {r['request_id']for r in children}!=registered:raise ValueError('SINGLE_NATIVE_DESCENDANTS')
  for child in children:
   cid=child['request_id'];record=self.record(cid)
   if isinstance(record,tuple):record=record[0]
   key=rid+'.'+calls[0]['id']
   if child['requester']!=parent['requester']or child['project_id']!=parent['project_id']or child['idempotency_key']!=key or any(record.get(k)!=v for k,v in {'parentId':rid,'actor':parent['requester'],'request':request,'stage':stage,'childKey':key}.items()):raise ValueError('SINGLE_NATIVE_CHILD')
   from .domain_resolvers import IMAGE,IMAGE_SINGLE,DOCUMENT
   route=IMAGE_SINGLE if self.family=='image'else DOCUMENT
   binding=record if self.family=='image'else {k:record[k]for k in ('variation','stage','request','childKey','actor','parentId')}
   refs=self.common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(cid,)).fetchall()
   if (child['request_type'],child['schema_version'])!=route[:2] or len(refs)!=1 or any(refs[0][k]!=v for k,v in {'domain':route[2],'schema_version':route[1],'reference_id':cid,'input_hash':digest(binding)}.items()):raise ValueError('SINGLE_NATIVE_CHILD_HASH')
   if self.common.db.execute('SELECT 1 FROM common_requests WHERE parent_id=?',(cid,)).fetchone():raise ValueError('SINGLE_NATIVE_UNEXPECTED_DESCENDANT')
  return OperationScope(dict(g),tuple(sorted(registered)),digest({'request':request,'selection':selection,'family':self.family}))

class NativeSingleBatchLifecycle(NativeMediaParentLifecycle):
 """Original native receipt/terminal/cleanup readers decide whether finish is safe."""
 pass

def native_child_guard(original,lifecycle,*,scratch_root,kind):
 if kind not in ('planning-attempt','portrait-stage'):raise ValueError('SINGLE_NATIVE_WORKSPACE_KIND')
 root=Path(scratch_root)
 if not root.is_absolute()or root.resolve()!=root:raise ValueError('SINGLE_NATIVE_SCRATCH_ROOT')
 def guard(claim):
  value=original(claim);g=value['guard'];rid=value['scopeRequestId']
  if rid!=g['requestId']or value['stageRequestId']!=claim.request_id:raise ValueError('SINGLE_NATIVE_GUARD')
  path=root/digest([claim.request_id,claim.attempt_id])if hasattr(claim,'attempt_id')else None
  _,state,_=lifecycle.ensure(rid,kind=kind,path=path)
  if state['guard']!=g:raise ValueError('SINGLE_NATIVE_GUARD_CHANGED')
  return value
 return guard

# Reuses original Coordinator, receipts, terminal, cleanup plus real Issuer and
# RequestCoverage. No process-global RegistryFactory installation.
bind_native_single_factory=bind_native_media_factory

class SingleBatchMetadataAllocations:
 """Original prepare references / canonical mirror, not legacy model execution.

canonical_result must read the service's durable receipt and independently
verify its Filex publication. It returns the exact original result, never bool.
 """
 def __init__(self,issued,persisted,scope_reader,canonical_result):
  if issued.legacy is not None:raise ValueError('SINGLE_METADATA_DOUBLE_LEGACY')
  self.issued,self.persisted,self.reader,self.result=issued,persisted,scope_reader,canonical_result
 def __getattr__(self,name):return getattr(self.issued,name)
 def entries(self,g):
  from .resident_frame_proof import checked
  import hashlib
  scoped=self.reader(g);values=[]
  for cid in scoped.request_ids:
   record=self.reader.record(cid)
   if isinstance(record,tuple):record=record[0]
   for original in self.persisted.entries(g,cid):
    # Allocation authority is the immutable original request, not mutable files.
    # Validate current contents below without changing the issued claim.
    value=dict(original)
    path=checked(value['path'])
    if path.exists():
     if self.reader.family!='image' or value['kind']!='portrait-stage' or not path.is_dir():raise ValueError('SINGLE_LEGACY_EXECUTION_PRESERVED')
     expected={}
     manifest=record.get('claim',{}).get('referenceManifest')
     if manifest:
      if manifest.get('manifestHash')!=digest({k:v for k,v in manifest.items()if k!='manifestHash'}):raise ValueError('SINGLE_REFERENCE_MANIFEST')
      for ref in manifest.get('references',[]):
       file=checked(ref['localPath'])
       if path not in file.parents or file in expected:raise ValueError('SINGLE_REFERENCE_PATH')
       expected[file]=ref['sha256']
     mirror=path/'result.json'
     if mirror.exists():
      result=self.result(cid)
      if not isinstance(result,dict) or result.get('sourceApplied')is not True or result.get('publicationScope')!='registered-npc-portrait-pair' or result.get('publication',{}).get('verified')is not True or result.get('requestId')!=cid:raise ValueError('SINGLE_CANONICAL_RESULT_REQUIRED')
      if mirror.is_symlink()or json.loads(mirror.read_text())!=result:raise ValueError('SINGLE_CANONICAL_MIRROR_CHANGED')
      expected[mirror]=hashlib.sha256(mirror.read_bytes()).hexdigest()
     files=set()
     for f in path.rglob('*'):
      if f.is_symlink():raise ValueError('SINGLE_METADATA_SYMLINK')
      if f.is_file():files.add(f)
      elif not f.is_dir():raise ValueError('SINGLE_METADATA_FILE_KIND')
     if files!=set(expected):raise ValueError('SINGLE_LEGACY_EXECUTION_PRESERVED')
     if any(hashlib.sha256(f.read_bytes()).hexdigest()!=h for f,h in expected.items()):raise ValueError('SINGLE_REFERENCE_CHANGED')
     # Keep persisted-stage authority/hash stable across mirror creation/cleanup.
    value['allocated']=True
    values.append(value)
  values+=self.issued.entries(g)
  if len({(v['kind'],v['path'])for v in values})!=len(values):raise ValueError('SINGLE_DUPLICATE_WORKSPACE')
  return values
 def __call__(self,g,kind,path):
  rows=[v for v in self.entries(g)if(v['kind'],v['path'])==(kind,path)]
  if len(rows)!=1:raise ValueError('SINGLE_EXACT_WORKSPACE')
  return rows[0]

def canonical_child_result(common,service,rid):
 """Read only the issued latest attempt's independently verified Filex result."""
 from ..contracts import Claim
 row=common.row(rid);matches=[]
 for grant in common.db.execute('SELECT * FROM resident_resource_grants WHERE request_id=?',(rid,)):
  current=str(row['fence'])==grant['attempt_id']
  if not current and common.db.execute("SELECT 1 FROM sqlite_master WHERE name='resident_reconciliations'").fetchone():
   recovery=common.db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id=?',(grant['grant_id'],)).fetchone()
   current=bool(recovery and recovery[0] and json.loads(recovery[0]).get('fence')==row['fence'])
  if current:matches.append(grant)
 if len(matches)!=1:raise ValueError('SINGLE_CANONICAL_ISSUED_ATTEMPT')
 grant=matches[0];value=json.loads(grant['claim_json']);value['input_refs']=tuple(value['input_refs']);claim=Claim(**value)
 if claim.request_id!=rid or claim.resource_grant!=grant['grant_id']:raise ValueError('SINGLE_CANONICAL_CLAIM_CHANGED')
 result=service.read(claim)['parentReceipt']
 if row['state']=='completed':
  receipt=json.loads(row['source_receipt'])if row['source_receipt']else None
  if not receipt or receipt.get('requestId')!=rid or receipt.get('receiptHash')!=digest(result):raise ValueError('SINGLE_CANONICAL_COMMON_RECEIPT')
 return result
