"""Explicit native workspace using opaque PDS documents/assets and local attempts.

No production selector. Filex credentials stay in the server storage port.
"""
import hashlib,json
from dataclasses import asdict
from pathlib import Path
from ..contracts import Receipt,PROFILES
from ..runtime import ReconciliationRequired
from .frame_workspace import HalfFrameWorkspace,digest,save
from ..backends.animation_native_png import validate_native_png_manifest,BINDING_KEYS,DIRECTIONS

RAW='ngs.animation-native-sequence-direction.v1'
ALPHA='ngs.animation-alpha-dependency.v1'
PIXEL='ngs.animation-native-sequence-pixel-job.v1'
def require(ok):
 if not ok:raise ValueError('NATIVE_WORKSPACE_BINDING')

class NativePNGWorkspace(HalfFrameWorkspace):
 def __init__(self,root,*,requests,storage,read_document,read_asset,raw_source,refresh_host):
  super().__init__(root,None)
  self.requests=requests;self.storage=storage;self.document=read_document;self.asset=read_asset
  self.raw_source=raw_source;self.refresh_host=refresh_host
 def _doc(self,mid,sha,rsha):
  value=self.document(mid)
  require(set(value)=={'manifest','receipt'} and digest(value['manifest'])==sha and digest(value['receipt'])==rsha
          and value['receipt']['hashes']=={mid:sha} and value['receipt']['status']=='committed')
  return value['manifest']
 def _assets(self,folder,ids):
  paths=[]
  for aid in ids:
   import re
   require(type(aid)is str and re.fullmatch('[a-f0-9]{64}\\.png',aid))
   data=self.asset(aid);require(hashlib.sha256(data).hexdigest()+'.png'==aid)
   p=folder/aid
   if not p.exists():p.write_bytes(data)
   require(not p.is_symlink()and p.read_bytes()==data);paths.append(str(p))
  return paths
 def stage(self,claim):
  task=claim.parameters;schema=task.get('schemaVersion');require(schema in (RAW,ALPHA,PIXEL))
  claim.validate(PROFILES['animation'if schema==RAW else'postprocess']);self.requests.authorize_publication(claim)
  folder=self._folder(claim)
  if folder.exists()or folder.is_symlink():raise ReconciliationRequired('EXISTING_ATTEMPT_REQUIRES_RECONCILIATION')
  folder.mkdir(mode=0o700);save(folder/'claim.json',asdict(claim))
  if schema==RAW:
   value=self.raw_source(claim);require(set(value)=={'job','reference','motion'})
   inputs={}
   for key,name in (('reference','reference.png'),('motion','motion.mp4')):
    data=value[key];frozen=task['frozenInputs'][key];require(hashlib.sha256(data).hexdigest()==frozen['sha256'])
    (folder/name).write_bytes(data);inputs[key]=dict(path=name,sha256=frozen['sha256'],ref=frozen['assetId'])
   save(folder/'animation-job.json',dict(claim=dict(requestId=claim.request_id,attemptId=claim.attempt_id,fencingToken=claim.fencing_token,expectedRevision=claim.expected_revision,parametersHash=digest(task),inputRefs=list(claim.input_refs)),job=value['job'],inputs=inputs))
  else:
   assets=folder/'inputs';assets.mkdir(mode=0o700);rows={}
   deps=[task]if schema==ALPHA else task['inputs']
   for dep in deps:
    d=dep['direction'];raw=self._doc(dep['rawManifestId'],dep['rawManifestHash'],dep['rawReceiptHash'])
    rawpaths=self._assets(assets,dep['rawAssetIds'])
    validate_native_png_manifest(raw,{k:raw[k]for k in BINDING_KEYS},[Path(p).read_bytes()for p in rawpaths],frame_count=raw['frameCount'],fps=raw['fps'])
    require([f['assetId']for f in raw['frames']]==dep['rawAssetIds'] and all(raw[k]==task[k]for k in ('npcId','generationId','posePreset','presetId'))and raw['direction']==d)
    row=dict(raw=raw,rawPaths=rawpaths,rawManifestId=dep['rawManifestId'])
    if schema==ALPHA:
     require(raw['requestId']==task['rawRequestId']and raw['sourceBindingHash']==task['sourceBindingHash'])
    else:
     alpha=self._doc(dep['alphaManifestId'],dep['alphaManifestHash'],dep['alphaReceiptHash'])
     require(alpha['rawManifestHash']==digest(raw)and alpha['rawManifestId']==dep['rawManifestId']and alpha['requestId']==dep['alphaRequestId']and alpha['direction']==d)
     row.update(alpha=alpha,alphaPaths=self._assets(assets,dep['alphaAssetIds']))
    require(d not in rows);rows[d]=row
   if schema==PIXEL:require(tuple(rows)==DIRECTIONS)
   save(folder/'native-stage.json',dict(claim=asdict(claim),directions=rows))
  self.requests.authorize_publication(claim);self.refresh_host();return folder
 def publish(self,claim,workspace,artifacts):
  require(workspace==self._folder(claim)and artifacts)
  root=workspace.resolve();paths=[Path(p)for p in artifacts]
  require(all(not p.is_symlink()and p.resolve().is_relative_to(root)and p.is_file()for p in paths))
  manifest=json.loads(paths[0].read_text());task=claim.parameters
  if task['schemaVersion']==RAW:
   self.raw_source(claim) # same frozen source, existing current authority before publish
   require(all(manifest.get(k)==task[k]for k in BINDING_KEYS))
  elif task['schemaVersion']==ALPHA:
   self._doc(task['rawManifestId'],task['rawManifestHash'],task['rawReceiptHash'])
   require(manifest.get('requestId')==claim.request_id and manifest.get('dependencyHash')==digest(task))
  else:
   for dep in task['inputs']:
    self._doc(dep['rawManifestId'],dep['rawManifestHash'],dep['rawReceiptHash'])
    self._doc(dep['alphaManifestId'],dep['alphaManifestHash'],dep['alphaReceiptHash'])
  phase={RAW:'animation-sequences',ALPHA:'animation-alpha',PIXEL:'animation-processed'}[task['schemaVersion']]
  mid='canonical-files/binari/npcs/{}/{}/{}/{}'.format(task['npcId'],phase,task['posePreset'],task['presetId'])
  if phase!='animation-processed':mid+='/'+task['direction']
  mid+='.json'
  ids=([f['assetId']for d in DIRECTIONS for f in manifest['directions'][d]['frames']]if phase=='animation-processed'else[f['assetId'if phase=='animation-sequences'else'alphaAssetId']for f in manifest['frames']])
  byid={hashlib.sha256(p.read_bytes()).hexdigest()+'.png':p for p in paths[1:]}
  require(set(byid)==set(ids))
  # Pure precommit validation, not a synthetic authority receipt. Do this before
  # the first upload so altered local outputs cannot be committed then refused.
  if task['schemaVersion']==RAW:
   validate_native_png_manifest(manifest,{k:task[k]for k in BINDING_KEYS},[byid[aid].read_bytes()for aid in ids],frame_count=task['frozenInputs']['motion']['frameCount'],fps=task['frozenInputs']['motion']['fps'])
  elif task['schemaVersion']==ALPHA:
   from ..backends.frame_native_manifests import build_alpha_manifest
   raw=self._doc(task['rawManifestId'],task['rawManifestHash'],task['rawReceiptHash'])
   rebuilt=build_alpha_manifest(raw,[self.asset(aid)for aid in task['rawAssetIds']],[byid[aid].read_bytes()for aid in ids],dependency_hash=digest(task),inference=manifest['inferenceProvenance'],raw_manifest_id=task['rawManifestId'],alpha_request_id=claim.request_id)
   require(rebuilt==manifest)
  else:
   from local.services.animation_requests.native_png_workflow import _pixel_assets
   require(_pixel_assets(task,manifest)==ids)
  packet=dict(path=mid,body=manifest,expectedHash=None)
  save(workspace/'publication-intent.json',dict(requestId=claim.request_id,manifestHash=digest(manifest),path=mid))
  for aid,p in byid.items():self.storage.upload_asset(claim,aid,p.read_bytes())
  receipt=self.storage.commit(claim,claim.expected_revision,[packet],{mid:None})
  require(receipt['hashes']=={mid:digest(manifest)})
  save(workspace/'publication-receipt.json',receipt);self.refresh_host()
  return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,tuple(ids),digest(manifest),True)
