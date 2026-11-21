"""Original NPC portrait + native SD pixel-pair generation in staged workspace.

The trusted existing pair module owns prompts, alpha validation and pixel policy.
The asset sink records only temporary bytes; publication/final CAS belongs to PDS
and the original Request parent. No caller-selected code or direct original writes.
"""
import hashlib,json
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
from .image_music_workspace import stage,output_directory,write_new,digest,safe_file
from ..contracts import ExecutionStopUnconfirmed

class StagedAssets:
 def __init__(self,root):self.root=root
 def register(self,raw,project,rid,provenance):
  sha=hashlib.sha256(raw).hexdigest();path=self.root/(sha+'.png')
  if path.exists():
   if path.is_symlink()or path.read_bytes()!=raw:raise ValueError('PAIR_STAGED_ASSET_CONFLICT')
  else:write_new(path,raw)
  return {'sha256':sha,'size':len(raw),'stagedPath':path.name,'publicationScope':'unpublished-worker-staging'}
 def verify(self,asset,expected):
  raw=safe_file(self.root,asset['stagedPath']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=expected['sha256']or len(raw)!=expected['size']:raise ValueError('PAIR_STAGED_ASSET_CHANGED')

class PortraitPairBackend:
 def __init__(self,runner_factory,pair_module,validate_output):
  if not callable(runner_factory)or not callable(validate_output)or not callable(getattr(pair_module,'generate_pixel',None)):raise ValueError('PAIR_TRUSTED_IMPLEMENTATION_REQUIRED')
  self.factory,self.pairs,self.validate=runner_factory,pair_module,validate_output;self._scope=None;self._proof=None
 def load(self):pass
 def unload(self):pass
 def execution_evidence(self,claim):return deepcopy(self._proof)if digest(asdict(claim))==self._scope else None
 def execute(self,claim,workspace,cancel):
  self._scope=digest(asdict(claim));self._proof=None
  root,packet,refs=stage(claim,workspace);record=claim.parameters
  if claim.capability!='image.generate'or record.get('schemaVersion')!='npc-workflow-portrait-single.v1'or record.get('posePreset')not in self.pairs.POSES:raise ValueError('PAIR_OFFICIAL_POSE_RECORD_REQUIRED')
  if record.get('regeneratePixelOnly')or record.get('reprocessedPixelSource'):raise ValueError('PAIR_FRESH_FULL_REQUEST_REQUIRED')
  native=packet.get('execution',{}).get('providerClaim')
  if not isinstance(native,dict)or native.get('providerRequestHash')!=digest(native['providerRequest']):raise ValueError('PAIR_NATIVE_CLAIM_INVALID')
  if native['providerRequest']['arguments'].get('referenced_image_paths',[])!=[str(p)for p in refs]:raise ValueError('PAIR_REFERENCE_BINDING')
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  folder=output_directory(root);runner=self.factory(root,cancel)
  try:
   raw,proof=runner.generate(native)
  except Exception as error:
   evidence=getattr(runner,'execution_evidence',lambda:None)()
   if not isinstance(error,ExecutionStopUnconfirmed) and isinstance(evidence,dict) and evidence.get('outcome')=='joined':
    self._proof=deepcopy(evidence);raise
   raise ExecutionStopUnconfirmed('PAIR_PORTRAIT_OUTCOME_UNKNOWN')from error
  self._proof=deepcopy(getattr(runner,'execution_evidence',lambda:None)())
  if proof.get('actualToolOutput')is not True or proof.get('sha256')!=hashlib.sha256(raw).hexdigest()or any(proof.get(k)!=native[k]for k in ('index','providerClaim','requestHash','providerRequestHash')):raise ValueError('PAIR_PORTRAIT_PROOF_INVALID')
  self.validate(raw,native['providerRequest']['outputContract'],'portrait')
  write_new(folder/'output.png',raw);write_new(folder/'provider-proof.json',json.dumps(proof,sort_keys=True).encode())
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  # Reuse the exact canonical SD prompt / fixed384 profile / six-field proof.
  # Existing code writes only beneath this folder and the staging-only sink.
  assets=StagedAssets(folder)
  self._proof=None
  try:pixel=self.pairs.generate_pixel(record,claim.request_id,folder,runner,assets,raw)
  except ExecutionStopUnconfirmed:raise
  except Exception as error:
   evidence=getattr(runner,'execution_evidence',lambda:None)()
   if isinstance(evidence,dict) and evidence.get('outcome')=='joined':
    self._proof=deepcopy(evidence);raise
   raise ExecutionStopUnconfirmed('PAIR_PIXEL_OUTCOME_REQUIRES_RECONCILIATION')from error
  self._proof=deepcopy(getattr(runner,'execution_evidence',lambda:None)())
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  names=('output.png','provider-proof.json','pixel-claim.json','pixel-source.png','pixel-output.png','pixel-result.json')
  files={name:{'sha256':hashlib.sha256(safe_file(folder,name).read_bytes()).hexdigest(),'bytes':safe_file(folder,name).stat().st_size}for name in names}
  manifest={'schemaVersion':'resident.portrait-pair.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'originalRecordHash':digest(record),'effectivePortraitClaim':native,'files':files}
  target=folder/'pair-manifest.json';write_new(target,json.dumps(manifest,sort_keys=True).encode())
  evidence=getattr(runner,'execution_evidence',lambda:None)()
  if isinstance(evidence,dict)and evidence.get('outcome')=='joined':self._proof=deepcopy(evidence)
  return (target,*(folder/name for name in names))

def native_pair_runner_factory(*,binary,runner_type,approved_output_roots):
 """Trusted composition uses original runner with cancellation-aware joined CLI."""
 from .image_native import NativeImageCLI
 roots=tuple(Path(p).resolve(strict=True)for p in approved_output_roots)
 def build(root,cancel):
  (root/'references').mkdir(exist_ok=True)
  (root/'artifacts/references').mkdir(exist_ok=True)
  client=NativeImageCLI(binary,root,root/'native-cli',cancel)
  original=runner_type(client,root/'native-journal',roots,(root,root/'references',root/'artifacts/references'))
  class PairRunner:
   def __init__(self):self.proofs=[];self.unknown=False
   def paths(self,claim):return original.paths(claim)
   def recover(self,claim):raise ExecutionStopUnconfirmed('PAIR_REQUIRES_FORMAL_RECOVERY')
   def generate(self,claim):
    if self.unknown:raise ExecutionStopUnconfirmed('PAIR_PREVIOUS_OUTCOME_UNKNOWN')
    client.process_evidence=None
    try:return original.generate(claim)
    except ExecutionStopUnconfirmed:self.unknown=True;raise
    finally:
     proof=client.process_evidence
     if not isinstance(proof,dict)or proof.get('outcome')!='joined':self.unknown=True
     else:self.proofs.append(deepcopy(proof))
   def execution_evidence(self):
    if self.unknown or not self.proofs:return None
    return {'outcome':'joined','processGroups':[p for x in self.proofs for p in x['processGroups']],'returnCodes':[p for x in self.proofs for p in x['returnCodes']]}
  return PairRunner()
 return build
