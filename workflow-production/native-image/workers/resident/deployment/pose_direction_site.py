"""Formal pose direction service. Native publication only; no provider execution."""
import json,hashlib,os,fcntl,base64
from pathlib import Path
from dataclasses import asdict
from .request_selector import digest
from .animation_frame_site import existing_root
from ..backends.image_music_workspace import safe_file
from ..adapters.frame_workspace import save
from ..contracts import Receipt

POSE=('npc.pose.generate-direction','ngs.pose-direction-intake.v1','pose.direction.requests')
SCHEMA='ngs.pose-direction-request.v1'
SCOPE='registered-npc-pose-direction'

class PoseDirectionService:
 def __init__(self,authority,*,scratch_root,journal_root,original,client,workflow,validate_bundle):
  self.authority=authority;self.scratch=existing_root(scratch_root);self.journal=existing_root(journal_root)
  self.original,self.client,self.workflow,self.validate_bundle=original,client,workflow,validate_bundle
 def folder(self,c):return self.scratch/digest([c.request_id,c.attempt_id])
 def path(self,c,suffix='receipt'):return self.journal/(digest([c.request_id,c.attempt_id])+'.'+suffix+'.json')
 def immutable(self,path,value):
  if path.exists() or path.is_symlink():
   if path.is_symlink() or json.loads(path.read_text())!=value:raise ValueError('POSE_DURABLE_PROOF_CONFLICT')
  else:save(path,value)
 def authorize(self,c):
  self.original(c);self.authority.authorize_publication(c)
  return {'requestId':c.request_id,'requiredSourceApplied':True,'publicationScope':SCOPE,'originalRecordHash':digest(dict(c.parameters))}
 def packet(self,c,packet):
  if not isinstance(packet,dict) or set(packet)!={'manifest'}:raise ValueError('POSE_PACKET_INVALID')
  if json.loads(safe_file(self.folder(c),'artifacts/pose-manifest.json').read_text())!=packet['manifest']:raise ValueError('POSE_MANIFEST_CHANGED')
 def submit(self,c,packet):self.authorize(c);return self.publish(c,packet)
 def publish(self,c,packet):
  fd=os.open(self.journal/(digest([c.request_id,c.attempt_id])+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX)
   request,state,step=self.original(c);self.packet(c,packet)
   if self.path(c).exists():
    saved=self.read(c)
    if saved['packetHash']!=digest(packet):raise ValueError('POSE_REPLAY_CHANGED')
    self.authority.finish_publication(c,digest(packet),saved['reservedReceipt']);return saved
   result,raw,output=self.validate_bundle(c,self.folder(c),request,state,step)
   reserved=self.authority.prepare_publication(c,digest(packet));client=self.client(c)
   intent_path=self.path(c,'intent')
   if intent_path.exists():
    intent=json.loads(safe_file(self.journal,intent_path.name).read_text())
    if intent.get('packetHash')!=digest(packet):raise ValueError('POSE_INTENT_CHANGED')
    wire=intent['wire'];receipt=client.receipt(c.request_id)
    if receipt is None:
     replay={**wire,'artifact':{**wire['artifact'],'base64':base64.b64encode(output).decode()},'sourceArtifact':{**wire['sourceArtifact'],'base64':base64.b64encode(raw).decode()}}
     receipt=client.api('POST','/v1/npcs/pose-views/publish',replay)
   else:
    def api(method,path,payload=None,raw=False):
     if method=='POST':
      if path!='/v1/npcs/pose-views/publish' or payload.get('requestId')!=c.request_id:raise ValueError('POSE_PUBLISH_SCOPE')
      metadata={**payload,'artifact':{k:v for k,v in payload['artifact'].items() if k!='base64'},'sourceArtifact':{k:v for k,v in payload['sourceArtifact'].items() if k!='base64'}}
      self.immutable(intent_path,{'packetHash':digest(packet),'wire':metadata})
     return client.api(method,path,payload,raw)
    native=self.workflow(c,api)
    receipt=native.publish_prepared(request,state,step,result,raw,output)
    wire=json.loads(safe_file(self.journal,intent_path.name).read_text())['wire']
   client.verify(wire,receipt)
   if client.receipt(c.request_id)!=receipt:raise ValueError('POSE_RECEIPT_READBACK_CHANGED')
   envelope={k:reserved[k]for k in ('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')}
   envelope.update(receiptKind='npc-pose-direction-service.v1',publicationHash=digest(packet),authority='filex',status='committed',verified=True,sourceReceipt=receipt)
   envelope['receiptHash']=digest(envelope)
   value={'requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'packetHash':digest(packet),'publicationScope':SCOPE,'verified':True,'sourceApplied':True,'revision':str(receipt['revision']),'assetRefs':[receipt['assetId']],'sourceReceipt':receipt,'reservedReceipt':envelope}
   self.immutable(self.path(c),value);self.authority.finish_publication(c,digest(packet),envelope);return value
  finally:os.close(fd)
 def read(self,c):
  self.original(c);value=json.loads(safe_file(self.journal,self.path(c).name).read_text())
  if any(value.get(k)!=v for k,v in {'requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'publicationScope':SCOPE,'verified':True,'sourceApplied':True}.items()):raise ValueError('POSE_RECEIPT_SCOPE_CHANGED')
  reserved=value['reservedReceipt']
  if reserved.get('receiptHash')!=digest({k:v for k,v in reserved.items()if k!='receiptHash'}) or reserved.get('sourceReceipt')!=value['sourceReceipt'] or reserved.get('publicationHash')!=value['packetHash']:raise ValueError('POSE_RESERVED_RECEIPT_CHANGED')
  intent=json.loads(safe_file(self.journal,self.path(c,'intent').name).read_text())
  if intent['packetHash']!=value['packetHash']:raise ValueError('POSE_INTENT_CHANGED')
  client=self.client(c);client.verify(intent['wire'],value['sourceReceipt'])
  if client.receipt(c.request_id)!=value['sourceReceipt']:raise ValueError('POSE_PDS_RECEIPT_CHANGED')
  return value
 def inspect_publication(self,c):
  if not self.path(c).exists():return None
  value=self.read(c);return Receipt(c.request_id,c.attempt_id,c.fencing_token,tuple(value['assetRefs']),value['revision'],True)
 def verify_publication(self,c,receipt):
  receipt.validate(c);value=self.read(c)
  if tuple(value['assetRefs'])!=receipt.asset_refs or value['revision']!=receipt.revision:raise ValueError('POSE_COMPLETION_CHANGED')
  return {'sourceReceipt':value['sourceReceipt'],'publication':{'requestId':c.request_id,'verified':True,'scope':SCOPE,'receipt':value['sourceReceipt']},'reservedReceipt':value['reservedReceipt']}
 def resume_publication(self,c,expected_hash):
  packet={'manifest':json.loads(safe_file(self.folder(c),'artifacts/pose-manifest.json').read_text())}
  if digest(packet)!=expected_hash or self.authority.verify_stopped(c)is not True:raise ValueError('POSE_RECOVERY_NOT_CONFIRMED')
  with self.authority.transaction():
   self.authority._lease(c)
   row=self.authority.common.db.execute('SELECT publication_hash FROM resident_publications WHERE grant_id=?',(c.resource_grant,)).fetchone()
   if row is None or row['publication_hash']!=expected_hash:raise ValueError('POSE_RECOVERY_RESERVATION_REQUIRED')
  self.publish(c,packet);return self.inspect_publication(c)


def pose_direction_domain_factory(*,pds_url,scratch_root,journal_root,ownership_root,workflow_root,
        admitted_request,preflight,cleanup,ownership_lifecycle=None,pds_pose_reader=None,direct_pds=False):
 from types import SimpleNamespace
 from .domain_resolvers import DomainResolvers
 from .request_selector import FormalPayloadSelector
 from ..adapters.pose_direction_publication import PosePublicationClient
 from ..adapters.ownership_guard import PinnedOwnershipGuardResolver
 from ..backends.pose_direction import compile_direction,FILES
 from ..backends.image_music_workspace import stage as staged
 from .portrait_pair_service import _canonical_claim,_manifest
 from local.services.pose_requests import worker as pose_worker
 from local.services.pose_requests.contract import validate,directions,is_regeneration,routing
 from local.services.npc_workflow import portrait_pairs as pairs
 from local.services.portrait.png_alpha import validate_output_contract
 from local.services import npc_ownership
 import tempfile,math
 scratch,journal,ownership,workroot=map(existing_root,(scratch_root,journal_root,ownership_root,workflow_root))
 def build(common,bridge,models,terminal):
  def original(c):
   rid=c.request_id;row=common.row(rid)
   saved=common.db.execute('SELECT * FROM pose_direction_requests WHERE id=?',(rid,)).fetchone()
   if not saved or row['request_type']!=POSE[0] or saved['parent_id']!=row['parent_id'] or saved['actor']!=row['requester']:raise ValueError('POSE_FORMAL_CHILD_REQUIRED')
   body=json.loads(saved['body'])
   refs=common.db.execute('SELECT * FROM common_payload_refs WHERE request_id=?',(rid,)).fetchall()
   if (body!=dict(c.parameters) or body.get('schemaVersion')!=SCHEMA or digest(body)!=saved['input_hash'] or body['parentRequestId']!=row['parent_id'] or rid!=row['parent_id']+'.'+body['direction']
       or len([r for r in refs if (r['domain'],r['schema_version'],r['reference_id'],r['input_hash'])==(POSE[2],POSE[1],rid,digest(body))])!=1):raise ValueError('POSE_CHILD_HASH_CHANGED')
   parent=common.row(row['parent_id']);source=common.db.execute('SELECT * FROM pose_intake_requests WHERE id=?',(row['parent_id'],)).fetchone()
   if source is None or source['actor']!=row['requester'] or parent['requester']!=row['requester'] or parent['project_id']!=row['project_id']:raise ValueError('POSE_PARENT_OWNER_CHANGED')
   request=json.loads(source['body']);validate(request)
   if parent['request_type']!=routing(request)[0] or parent['schema_version']!='ngs.pose-intake.v1' or parent['source']!='pose.requests':raise ValueError('POSE_PARENT_ROUTE_CHANGED')
   if digest(request)!=source['input_hash'] or body['parentInputHash']!=source['input_hash']:raise ValueError('POSE_PARENT_INPUT_CHANGED')
   request={**request,'requestId':row['parent_id']};full=json.loads(source['state'] or '{}')
   if full.get('executionMode')!='external-directions.v1' or full.get('requestHash')!=digest(request):raise ValueError('POSE_PARENT_BINDING_REQUIRED')
   expected=[(d,request['requestId']+'.'+d)for d in directions(request)]
   if [(s.get('direction'),s.get('requestId'))for s in full.get('steps',[])]!=expected:raise ValueError('POSE_STEPS_CHANGED')
   steps=[s for s in full['steps'] if s['requestId']==rid]
   if len(steps)!=1:raise ValueError('POSE_STEP_NOT_REGISTERED')
   state={k:full[k]for k in ('context','selection','sourceAssetId','incarnationId','requestHash','targetSlotHash','rearReference')if k in full}
   if body.get('parentStateHash')!=digest(state) or full.get('immutableStateHash')!=digest(state):raise ValueError('POSE_PARENT_STATE_CHANGED')
   return request,state,{'direction':body['direction'],'requestId':rid}
  def session(c):
   request,state,step=original(c);npc=request['npcId'];parent=request['requestId'];path=ownership/(digest([npc,parent])+'.json')
   if path.is_symlink() or not path.is_file():raise ValueError('POSE_ORIGINAL_PARENT_PIN_REQUIRED')
   return npc_ownership.Session(ownership,npc,parent,call=lambda *a,**k:(_ for _ in ()).throw(ValueError('POSE_NEW_ADMISSION_FORBIDDEN')))
  pinned_guard=PinnedOwnershipGuardResolver(session)
  def guard(c):
   if ownership_lifecycle is not None:
    request,_,_=original(c)
    path=scratch/digest([c.request_id,c.attempt_id])if hasattr(c,'attempt_id')else None
    ownership_lifecycle.ensure(request['requestId'],kind='pose-request',path=path)
   return pinned_guard(c)
  def client(c):return PosePublicationClient(pds_url,lambda:guard(c)['guard'],c.request_id)
  def workflow(c,api):
   native=pose_worker.PoseWorkflow(workroot,api=api,ports=lambda *a:(_ for _ in ()).throw(ValueError('POSE_SERVICE_GENERATION_FORBIDDEN')),context_reader=client(c).context)
   publish=native.publish_prepared
   def checked_publish(request,state,step,result,raw,output):
    if step['direction']in ('ne','nw') and not is_regeneration(request):
     rear=native.published_rear(request,state,native.npc(request))
     if tuple(c.input_refs)[-1]!='pds:asset:'+hashlib.sha256(rear).hexdigest()+'.png':raise ValueError('POSE_REAR_REFERENCE_CHANGED')
    return publish(request,state,step,result,raw,output)
   native.publish_prepared=checked_publish
   return native
  def source(c,folder):
   request,state,step=original(c)
   if Path(folder)!=scratch/digest([c.request_id,c.attempt_id]):raise ValueError('POSE_SOURCE_WORKSPACE_CHANGED')
   port=client(c);native=workflow(c,port.api);npc=native.npc(request)
   if npc.get('creationBasis',{}).get('requestId')!=state['incarnationId'] or npc.get('portraitSets',{}).get(request['posePreset'],{}).get(request['referenceType']+'AssetId')!=state['sourceAssetId']:raise ValueError('POSE_CENTER_CHANGED')
   native.check_target(request,state,npc)
   assets=[state['sourceAssetId']]
   if step['direction']in ('ne','nw'):
    if is_regeneration(request):assets.append(state['rearReference']['assetId'])
    else:
     rear=native.published_rear(request,state,npc);assets.append(hashlib.sha256(rear).hexdigest()+'.png')
   if tuple('pds:asset:'+a for a in assets)!=tuple(c.input_refs):raise ValueError('POSE_INPUT_REFS_CHANGED')
   files=[{'ref':'pds:asset:'+a,'path':str(i)+'.png','sha256':a[:-4],'bytes':port.asset(a)}for i,a in enumerate(assets)]
   return {'revision':c.expected_revision,'projectId':request['projectId'],'targetId':request['npcId'],'execution':{'request':request,'state':state,'step':step},'inputFiles':files}
  def validate_bundle(c,folder,request,state,step):
   root,execution,paths=staged(c,folder)
   if json.loads(safe_file(root,'claim.json').read_text())!=json.loads(json.dumps(asdict(c))):raise ValueError('POSE_WORKSPACE_OWNER_CHANGED')
   e=execution['execution']
   if e!={'request':request,'state':state,'step':step}:raise ValueError('POSE_EXECUTION_BINDING_CHANGED')
   manifest=json.loads(safe_file(root,'artifacts/pose-manifest.json').read_text())
   expected={'schemaVersion':'resident.pose-direction.v1','requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'parentRequestId':request['requestId'],'direction':step['direction'],'parentStateHash':digest(state)}
   if set(manifest)!=set(expected)|{'files'} or any(manifest.get(k)!=v for k,v in expected.items()) or set(manifest['files'])!=set(FILES):raise ValueError('POSE_ARTIFACT_MANIFEST_CHANGED')
   data={n:safe_file(root,'artifacts/'+n).read_bytes() for n in FILES}
   if any(manifest['files'][n]!={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}for n,raw in data.items()):raise ValueError('POSE_ARTIFACT_BYTES_CHANGED')
   native=json.loads(data['provider-claim.json']);result=json.loads(data['result.json'])
   manifest_refs=_manifest(native['referenceManifest'],root,request['npcId'])
   if native.get('providerRequestHash')!=digest(native['providerRequest']) or native['providerRequest'].get('metadata',{}).get('referenceManifest')!=manifest_refs or native['providerRequest']['arguments'].get('referenced_image_paths')!=[r['localPath']for r in manifest_refs['references']]:raise ValueError('POSE_NATIVE_REFERENCE_CHANGED')
   # Compile exact native prompt/reference semantics independently. Only local
   # reference relocation differs; no service call invokes a model/provider.
   with tempfile.TemporaryDirectory(dir=journal)as tmp:
    expected_native,desc,_=compile_direction(request,state,step,Path(tmp),[p.read_bytes()for p in paths],pairs,pose_worker)
    if _canonical_claim(native)!=_canonical_claim(expected_native):raise ValueError('POSE_NATIVE_CLAIM_CHANGED')
   proof=result['providerProof'];raw=data['source.png'];output=data['output.png']
   if proof.get('actualToolOutput')is not True or proof.get('sha256')!=hashlib.sha256(raw).hexdigest() or any(proof.get(k)!=native[k]for k in ('index','providerClaim','providerRequestHash','requestHash')):raise ValueError('POSE_NATIVE_PROOF_CHANGED')
   validate_output_contract(raw,desc['outputContract']);normalized,post=pairs.postprocess_pixel(raw)if request['referenceType']=='pixel'else(raw,None)
   expected_result={'sha256':hashlib.sha256(output).hexdigest(),'sourceSha256':hashlib.sha256(raw).hexdigest(),'postprocess':post,'promptHash':digest(desc['arguments']['prompt']),'actualPrompt':desc['arguments']['prompt'],'references':native['referenceManifest'],'modelCalls':1,'technicalRetries':0}
   if output!=normalized or any(result.get(k)!=v for k,v in expected_result.items()) or type(result.get('elapsedSeconds'))not in (int,float) or not math.isfinite(result['elapsedSeconds']) or result['elapsedSeconds']<0:raise ValueError('POSE_NORMALIZATION_CHANGED')
   return result,raw,output
  if type(direct_pds)is not bool:raise ValueError('POSE_DIRECT_MODE_INVALID')
  reader=pds_pose_reader
  if reader is None and direct_pds:
   from ..adapters.pose_direction_pds import DirectPoseDirectionClient
   reader=DirectPoseDirectionClient(pds_url,ownership_guard=lambda c:guard(c)['guard']).read
  service_type=PoseDirectionService;extra={}
  if reader is not None:
   if not callable(reader):raise ValueError('POSE_DIRECT_READER_REQUIRED')
   service_type=DirectPoseDirectionService;extra={'pds_pose_reader':reader,'parent_guard':lambda c:guard(c)['guard']}
  service=service_type(bridge,scratch_root=scratch,journal_root=journal,original=original,client=client,workflow=workflow,validate_bundle=validate_bundle,**extra)
  def settled_receipt(c):
   # Read a historical canonical receipt only. Never use the active publish
   # guard, whose native ensure intentionally rejects an already released pin.
   request,_,_=original(c)
   original_pin=session(c).path
   if original_pin.is_symlink()or not original_pin.is_file():raise ValueError('POSE_ORIGINAL_PARENT_PIN_REQUIRED')
   if json.loads(original_pin.read_text()).get('released')is not True:
    return service.read(c)['sourceReceipt']
   reader=getattr(ownership_lifecycle,'released_session',None)
   if not callable(reader):raise ValueError('POSE_RELEASED_RECEIPT_READER_REQUIRED')
   pin=reader(request['requestId'])
   if pin.path.is_symlink()or not pin.path.is_file():raise ValueError('POSE_ORIGINAL_PARENT_PIN_REQUIRED')
   saved=json.loads(pin.path.read_text());g=saved.get('guard')
   if (saved.get('released')is not True or not isinstance(g,dict)
       or g.get('npcId')!=request['npcId']or g.get('requestId')!=request['requestId']
       or any(saved.get(k)!=g.get(k)for k in ('npcId','generationId','requestId'))):
    raise ValueError('POSE_RELEASED_RECEIPT_SCOPE_CHANGED')
   value=PosePublicationClient(pds_url,lambda:dict(g),c.request_id).receipt(c.request_id)
   if json.loads(pin.path.read_text())!=saved:raise ValueError('POSE_RELEASED_RECEIPT_SCOPE_CHANGED')
   return value
  service.settled_receipt_reader=settled_receipt

  def source_metadata(c):
   value=source(c,scratch/digest([c.request_id,c.attempt_id]))
   return {**value,'inputFiles':[{**{k:v for k,v in item.items() if k!='bytes'},'assetId':item['sha256']+'.png'} for item in value['inputFiles']]}
  service.source=source_metadata
  def resolve(row,ref):
   saved=common.db.execute('SELECT body FROM pose_direction_requests WHERE id=?',(row['request_id'],)).fetchone()
   body=json.loads(saved[0])if saved else {};hint=SimpleNamespace(request_id=row['request_id'],parameters=body)
   request,state,step=original(hint);native=workflow(hint,client(hint).api);npc=native.npc(request);assets=[state['sourceAssetId']]
   if step['direction']in ('ne','nw'):
    if is_regeneration(request):assets.append(state['rearReference']['assetId'])
    else:assets.append(hashlib.sha256(native.published_rear(request,state,npc)).hexdigest()+'.png')
   return {'requestId':row['request_id'],'parameters':body,'inputRefs':['pds:asset:'+a for a in assets],'expectedRevision':str(state['context']['npcRevision']),'binding':{'projectId':row['project_id'],'targetId':request['npcId'],'expectedRevision':str(state['context']['npcRevision'])}}
  selector=FormalPayloadSelector(common,{POSE:('image.generate',resolve,lambda v:True)},ready=lambda r:admitted_request(r)is True)
  def verify_child_publication(request,state,step,view):
   from ..contracts import Claim
   rid=step['requestId'];row=common.row(rid)
   if view.get('requestId')!=rid or view.get('state')!='completed' or row['state']!='completed' or row['parent_id']!=request['requestId']:raise ValueError('POSE_PARENT_OBSERVER_CHILD_CHANGED')
   matches=[]
   for grant in common.db.execute('SELECT claim_json FROM resident_resource_grants WHERE request_id=?',(rid,)):
    wire=json.loads(grant[0]);wire['input_refs']=tuple(wire['input_refs']);c=Claim(**wire)
    if service.path(c).exists():matches.append(c)
   if len(matches)!=1:raise ValueError('POSE_CHILD_CANONICAL_ATTEMPT_REQUIRED')
   c=matches[0]
   if str(row['fence'])!=c.attempt_id:
    recovery=common.db.execute('SELECT recovery_json FROM resident_reconciliations WHERE grant_id=?',(c.resource_grant,)).fetchone()
    if recovery is None or not recovery[0] or json.loads(recovery[0]).get('fence')!=row['fence']:raise ValueError('POSE_CHILD_CURRENT_FENCE_CHANGED')
   expected_request,expected_state,expected_step=original(c)
   if expected_request!=request or expected_step!={'direction':step['direction'],'requestId':rid} or digest(expected_state)!=c.parameters['parentStateHash'] or bridge.verify_stopped(c)is not True:raise ValueError('POSE_CHILD_COMPLETION_UNCONFIRMED')
   receipt=service.inspect_publication(c);service.verify_publication(c,receipt)
   return service.read(c)['sourceReceipt']

  def clean(c):original(c);return cleanup(c,scratch/digest([c.request_id,c.attempt_id]))is True
  return {'capabilities':('image.generate',),'selector':selector,'pose_direction_service':service,'verify_child_publication':verify_child_publication,'guard_resolver':guard,'media_source':source,'verify_publication':service.verify_publication,'verify_recovery_publication':service.verify_publication,'inspect_publication':service.inspect_publication,'resume_publication':service.resume_publication,'cleanup':clean,'initialize_schema':lambda:None,'preflight':preflight,'publicationScope':SCOPE}
 return build

class DirectPoseDirectionService(PoseDirectionService):
 """Opt-in metadata-only Request authority; PDS validates bytes and publishes."""
 def __init__(self,*args,pds_pose_reader,parent_guard,**kwargs):
  super().__init__(*args,**kwargs)
  if not callable(pds_pose_reader)or not callable(parent_guard):raise ValueError('POSE_DIRECT_PORTS_REQUIRED')
  self.pds_pose_reader,self.parent_guard=pds_pose_reader,parent_guard
 def metadata(self,c,packet):
  from ..backends.pose_direction import FILES
  import re
  request,state,step=self.original(c)
  if not isinstance(packet,dict)or set(packet)!={'manifest'}:raise ValueError('POSE_PACKET_INVALID')
  m=packet['manifest'];expected={'schemaVersion':'resident.pose-direction.v1','requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'parentRequestId':request['requestId'],'direction':step['direction'],'parentStateHash':digest(state)}
  if not isinstance(m,dict)or set(m)!=set(expected)|{'files'}or any(m.get(k)!=v for k,v in expected.items())or not isinstance(m['files'],dict)or set(m['files'])!=set(FILES):raise ValueError('POSE_MANIFEST_SCOPE_CHANGED')
  for v in m['files'].values():
   if not isinstance(v,dict)or set(v)!={'sha256','bytes'}or type(v['bytes'])is not int or not 0<v['bytes']<=32*1024*1024 or not re.fullmatch('[a-f0-9]{64}',v.get('sha256','')):raise ValueError('POSE_FILE_METADATA_INVALID')
  return {'request':request,'state':state,'step':step}
 def binding(self,c,record,reservation):
  g=self.parent_guard(c)
  if not isinstance(g,dict)or set(g)!={'npcId','generationId','requestId','leaseToken'}or g['npcId']!=record['request']['npcId']or g['requestId']!=record['request']['requestId']:raise ValueError('POSE_DIRECT_GUARD_CHANGED')
  return {'reservation':reservation,'record':record,'parentGuard':g}
 def prepare(self,c,packet):
  record=self.metadata(c,packet)
  with self.authority.transaction():
   self.authority.authorize_publication(c);reservation=self.authority.prepare_publication(c,digest(packet))
  self.immutable(self.path(c,'metadata'),packet)
  return self.binding(c,record,reservation)
 def read_reservation(self,c,packet_hash):
  with self.authority.transaction():
   self.authority.authorize_publication(c)
   row=self.authority.common.db.execute('SELECT publication_hash FROM resident_publications WHERE grant_id=?',(c.resource_grant,)).fetchone()
   if row is None or row[0]!=packet_hash:raise ValueError('POSE_DIRECT_RESERVATION_REQUIRED')
   p=self.path(c,'metadata')
   if p.is_symlink()or not p.is_file():raise ValueError('POSE_DIRECT_INTENT_REQUIRED')
   packet=json.loads(p.read_text());record=self.metadata(c,packet)
   if digest(packet)!=packet_hash:raise ValueError('POSE_DIRECT_INTENT_CHANGED')
   reservation=self.authority.prepare_publication(c,packet_hash)
  return self.binding(c,record,reservation)
 def submit(self,c,packet):raise ValueError('POSE_DIRECT_PDS_UPLOAD_REQUIRED')
 def publish(self,c,packet):raise ValueError('POSE_DIRECT_PDS_UPLOAD_REQUIRED')
 def finish(self,c,packet):
  record=self.metadata(c,packet);reserved=self.read_reservation(c,digest(packet))['reservation']
  value=self.pds_pose_reader(c,digest(packet));expected={'requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'packetHash':digest(packet)}
  if not isinstance(value,dict)or set(value)!=set(expected)|{'sourceReceipt','wire'}or any(value.get(k)!=v for k,v in expected.items()):raise ValueError('POSE_DIRECT_READBACK_SCOPE')
  wire,receipt=value['wire'],value['sourceReceipt'];request,state,step=record['request'],record['state'],record['step'];files=packet['manifest']['files']
  slot={'posePreset':request['posePreset'],'referenceType':request['referenceType'],'direction':step['direction'],'sourceAssetId':state['sourceAssetId']}
  if wire.get('requestId')!=c.request_id or wire.get('npcId')!=request['npcId']or wire.get('projectId')!=request['projectId']or wire.get('slot')!=slot:raise ValueError('POSE_DIRECT_SLOT_CHANGED')
  for key,name in (('artifact','output.png'),('sourceArtifact','source.png')):
   if wire.get(key)!={'sha256':files[name]['sha256'],'bytes':files[name]['bytes'],'mimeType':'image/png'}:raise ValueError('POSE_DIRECT_ARTIFACT_CHANGED')
  client=self.client(c);client.verify(wire,receipt)
  if client.receipt(c.request_id)!=receipt:raise ValueError('POSE_RECEIPT_READBACK_CHANGED')
  envelope={k:reserved[k]for k in ('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')}
  envelope.update(receiptKind='npc-pose-direction-service.v1',publicationHash=digest(packet),authority='filex',status='committed',verified=True,sourceReceipt=receipt);envelope['receiptHash']=digest(envelope)
  saved={**expected,'publicationScope':SCOPE,'verified':True,'sourceApplied':True,'revision':str(receipt['revision']),'assetRefs':[receipt['assetId']],'sourceReceipt':receipt,'reservedReceipt':envelope}
  self.immutable(self.path(c,'intent'),{'packetHash':digest(packet),'wire':wire});self.immutable(self.path(c),saved)
  self.authority.finish_publication(c,digest(packet),envelope)
  return {'accepted':True}
 def resume_publication(self,c,expected_hash):
  p=self.path(c,'metadata')
  if p.is_symlink()or not p.is_file():raise ValueError('POSE_DIRECT_INTENT_REQUIRED')
  packet=json.loads(p.read_text())
  if digest(packet)!=expected_hash:raise ValueError('POSE_RECOVERY_HASH_CHANGED')
  self.finish(c,packet);return self.inspect_publication(c)
