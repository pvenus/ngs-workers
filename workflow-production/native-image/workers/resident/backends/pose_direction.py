"""One formal rotation child. Only staged bytes in, staged files out."""
import hashlib,json,time
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
from ..contracts import ExecutionStopUnconfirmed
from .image_music_workspace import stage,safe_file,write_new,digest

FILES=('provider-claim.json','source.png','output.png','result.json')
def compile_direction(request,state,step,folder,refs,pairs,pose_worker):
 if step['requestId']!=request['requestId']+'.'+step['direction'] or step['direction']not in ('n','ne','e','se','s','sw','w','nw'):raise ValueError('POSE_CHILD_BINDING')
 has_rear=step['direction']in ('ne','nw')
 if len(refs)!=(2 if has_rear else 1):raise ValueError('POSE_REFERENCE_COUNT')
 if hashlib.sha256(refs[0]).hexdigest()+'.png'!=state['sourceAssetId']:raise ValueError('POSE_CENTER_HASH')
 binding={'parentRequestId':request['requestId'],'direction':step['direction'],'sourceAssetId':state['sourceAssetId'],'contextHash':digest(state['context'])}
 manifest=pairs.reference(refs[0],request['npcId'],Path(folder)/'references',binding)
 if has_rear:
  back=pairs.reference(refs[1],request['npcId'],Path(folder)/'references',{**binding,'role':'rear-consistency'})
  manifest={'references':manifest['references']+back['references']};manifest['manifestHash']=digest(manifest)
 descriptor=pairs.descriptor(pose_worker.prompt(request,state,step['direction'],has_rear),manifest)
 native={'index':0,'providerClaim':'claim.'+digest([step['requestId'],'rotation'])[:48],'providerRequest':descriptor,'providerRequestHash':digest(descriptor),'requestHash':digest(binding),'referenceManifest':manifest}
 return native,descriptor,binding

class PoseDirectionBackend:
 def __init__(self,runner_factory,pairs,pose_worker,validate_output):
  self.factory,self.pairs,self.pose_worker,self.validate=runner_factory,pairs,pose_worker,validate_output;self.proof=None;self.scope=None
 def load(self):pass
 def unload(self):pass
 def execution_evidence(self,claim):return deepcopy(self.proof)if self.scope==digest(asdict(claim))else None
 def execute(self,claim,workspace,cancel):
  self.proof=None;self.scope=digest(asdict(claim));root,packet,paths=stage(claim,workspace)
  if claim.capability!='image.generate'or claim.parameters.get('schemaVersion')!='ngs.pose-direction-request.v1':raise ValueError('POSE_DIRECTION_REQUEST_REQUIRED')
  e=packet['execution'];request,state,step=e['request'],e['state'],e['step']
  if (claim.request_id!=step['requestId']or claim.parameters['parentRequestId']!=request['requestId']or claim.parameters['direction']!=step['direction']):raise ValueError('POSE_DIRECTION_SCOPE')
  if claim.parameters.get('parentStateHash')!=digest(state):raise ValueError('POSE_PARENT_STATE_CHANGED')
  folder=root/'artifacts';folder.mkdir(mode=0o700)
  native,descriptor,binding=compile_direction(request,state,step,folder,[p.read_bytes()for p in paths],self.pairs,self.pose_worker)
  write_new(folder/'provider-claim.json',json.dumps(native,sort_keys=True).encode())
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  runner=self.factory(root,cancel);started=time.time()
  try:raw,proof=runner.generate(native)
  except Exception as error:
   evidence=getattr(runner,'execution_evidence',lambda:None)()
   if not isinstance(error,ExecutionStopUnconfirmed) and isinstance(evidence,dict) and evidence.get('outcome')=='joined':
    self.proof=deepcopy(evidence);raise
   raise ExecutionStopUnconfirmed('POSE_NATIVE_OUTCOME_UNKNOWN')from error
  evidence=getattr(runner,'execution_evidence',lambda:None)()
  if isinstance(evidence,dict)and evidence.get('outcome')=='joined':self.proof=deepcopy(evidence)
  if proof.get('actualToolOutput')is not True or proof.get('sha256')!=hashlib.sha256(raw).hexdigest()or any(proof.get(k)!=native[k]for k in ('index','providerClaim','providerRequestHash','requestHash')):raise ValueError('POSE_PROVIDER_PROOF_INVALID')
  self.validate(raw,descriptor['outputContract'])
  output,post=self.pairs.postprocess_pixel(raw)if request['referenceType']=='pixel'else(raw,None)
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  result={'sha256':hashlib.sha256(output).hexdigest(),'sourceSha256':hashlib.sha256(raw).hexdigest(),'providerProof':proof,'postprocess':post,'promptHash':digest(descriptor['arguments']['prompt']),'actualPrompt':descriptor['arguments']['prompt'],'references':native['referenceManifest'],'elapsedSeconds':round(time.time()-started,3),'modelCalls':1,'technicalRetries':0}
  for name,data in [('source.png',raw),('output.png',output),('result.json',json.dumps(result,sort_keys=True).encode())]:write_new(folder/name,data)
  files={name:{'sha256':hashlib.sha256(safe_file(folder,name).read_bytes()).hexdigest(),'bytes':safe_file(folder,name).stat().st_size}for name in FILES}
  manifest={'schemaVersion':'resident.pose-direction.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'parentRequestId':request['requestId'],'direction':step['direction'],'parentStateHash':digest(state),'files':files}
  target=folder/'pose-manifest.json';write_new(target,json.dumps(manifest,sort_keys=True).encode())
  return (target,*(folder/n for n in FILES))

def native_pose_runner_factory(*,binary,runner_type,approved_output_roots):
 from .image_native import NativeImageCLI
 roots=tuple(Path(p).resolve(strict=True)for p in approved_output_roots)
 class PoseRunner(runner_type):
  def reference_keys(self,refs):
   if len(refs)>2 or len({r.get('npcId')for r in refs})!=1:raise ValueError('POSE_REFERENCE_SET_INVALID')
   return [(r.get('npcId'),r.get('sha256'))for r in refs]
 def build(root,cancel):
  client=NativeImageCLI(binary,root,root/'native-cli',cancel)
  runner=PoseRunner(client,root/'native-journal',roots,(root/'artifacts/references',))
  class Port:
   def generate(self,claim):
    client.process_evidence=None
    return runner.generate(claim)
   def execution_evidence(self):return deepcopy(client.process_evidence)
  return Port()
 return build
