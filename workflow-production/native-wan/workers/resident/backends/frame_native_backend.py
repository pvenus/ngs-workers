"""Native alpha/pixel resident adapter. Existing all-direction pixel policy."""
import hashlib,json
from pathlib import Path
from dataclasses import asdict
from ..adapters.frame_workspace import digest,save
from .animation_native_png import BINDING_KEYS,DIRECTIONS
from .frame_native_manifests import build_alpha_manifest,build_pixel_manifest
from .frame_native_png import process_directions

class NativeFrameBackend:
 requires_model=True
 release_model_after_execute=True
 def __init__(self,alpha_model,*,inference):self.alpha=alpha_model;self.inference=inference;self.entered=None
 def requires_model_for(self,claim):return claim.capability=='image.process'
 def set_model_lease(self,lease):self.alpha.set_model_lease(lease)
 def load(self):self.alpha.load()
 def unload(self):self.alpha.unload()
 def model_unload_evidence(self,lease):return self.alpha.model_unload_evidence(lease)
 def execution_evidence(self,claim):
  if self.entered!=claim:return None
  return self.alpha.execution_evidence(claim)if self.requires_model_for(claim)else{'outcome':'joined','processGroups':[],'returnCodes':[]}
 def execute(self,claim,workspace,cancel):
  self.entered=None;root=Path(workspace);stage=root/'native-stage.json'
  if stage.is_symlink():raise ValueError('NATIVE_FRAME_STAGE_CHANGED')
  value=json.loads(stage.read_text())
  if value['claim']!=json.loads(json.dumps(asdict(claim))):raise ValueError('NATIVE_FRAME_STAGE_CHANGED')
  rows=value['directions'];task=claim.parameters;self.entered=claim
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  def checked(names):
   paths=[Path(p)for p in names]
   if any(p.is_symlink()or not p.resolve().is_relative_to(root.resolve())or not p.is_file()for p in paths):raise ValueError('NATIVE_FRAME_STAGE_CHANGED')
   return paths
  if claim.capability=='image.process':
   if task['schemaVersion']!='ngs.animation-alpha-dependency.v1'or set(rows)!={task['direction']}:raise ValueError('NATIVE_FRAME_STAGE_CHANGED')
   row=rows[task['direction']];paths=checked(row['rawPaths']);out=root/'alpha-output';out.mkdir(mode=0o700)
   alpha=self.alpha.prepare(claim,paths,out,cancel)
   manifest=build_alpha_manifest(row['raw'],[p.read_bytes()for p in paths],[p.read_bytes()for p in alpha],dependency_hash=digest(task),inference={**self.inference,'inferenceRequestId':claim.request_id},raw_manifest_id=row['rawManifestId'],alpha_request_id=claim.request_id)
   artifacts=list(alpha)
  else:
   if claim.capability!='frames.extract'or task['schemaVersion']!='ngs.animation-native-sequence-pixel-job.v1'or set(rows)!=set(DIRECTIONS):raise ValueError('NATIVE_FRAME_STAGE_CHANGED')
   cohort={};alphas={}
   for d in DIRECTIONS:
    row=rows[d];raw=row['raw'];alpha=row['alpha'];rp=checked(row['rawPaths']);ap=checked(row['alphaPaths'])
    rebuilt=build_alpha_manifest(raw,[p.read_bytes()for p in rp],[p.read_bytes()for p in ap],dependency_hash=alpha['dependencyHash'],inference=alpha['inferenceProvenance'],raw_manifest_id=row['rawManifestId'],alpha_request_id=alpha['requestId'])
    if rebuilt!=alpha:raise ValueError('NATIVE_FRAME_ALPHA_CHANGED')
    alphas[d]=alpha
    cohort[d]=dict(sourceAssetId=row['rawManifestId'],frameCount=raw['frameCount'],fps=raw['fps'],rawFrames=[str(p)for p in rp],alphaFrames=[str(p)for p in ap],frameTimes=[i/raw['fps']for i in range(raw['frameCount'])],trace=dict(sourceBinding={k:raw[k]for k in BINDING_KEYS},rawSha256=[f['sha256']for f in raw['frames']],alphaSha256=[f['sha256']for f in alpha['frames']]))
   result=process_directions(cohort,root/'pixel-output');manifest=build_pixel_manifest(result,alphas)
   ids={f['assetId']for row in manifest['directions'].values()for f in row['frames']}
   artifacts=[Path(p)for aid,p in result['artifacts'].items()if aid+'.png' in ids]
  if cancel.is_set():raise RuntimeError('LEASE_LOST')
  path=root/'native-output.json';save(path,manifest)
  return (path,*artifacts)
