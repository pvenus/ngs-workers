"""PDS media-artifact draft adapter. No canonical NPC selection or Filex access."""
import base64,hashlib,json,os,re,urllib.error,urllib.parse,urllib.request
from dataclasses import asdict
from pathlib import Path
from ..contracts import Receipt
from ..runtime import ReconciliationRequired
from ..backends.image_music_workspace import digest,write_new,safe_file,stage as validate_stage
from .pds_category_files import NoRedirect

class MediaArtifactClient:
 def __init__(self,base_url,token,*,timeout=30):
  p=urllib.parse.urlsplit(base_url)
  if p.scheme!='http'or p.hostname!='127.0.0.1'or not p.port or p.path not in ('','/')or p.query or p.fragment or p.username or p.password:raise ValueError('MEDIA_PDS_LOOPBACK_REQUIRED')
  if not token or '\n'in token or '\r'in token:raise ValueError('MEDIA_PDS_TOKEN_REQUIRED')
  self.base=base_url.rstrip('/');self.token=token;self.timeout=timeout
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def call(self,path,payload=None,raw=False,missing=False):
  req=urllib.request.Request(self.base+path,data=json.dumps(payload,separators=(',',':')).encode()if payload is not None else None,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
  try:
   with self.opener.open(req,timeout=self.timeout)as response:data=response.read(64*1024*1024+1)
  except urllib.error.HTTPError as error:
   if missing and error.code==404:return None
   try:code=json.loads(error.read(65536)).get('error')
   except (ValueError,AttributeError):code=None
   if not isinstance(code,str)or not re.fullmatch('[A-Z0-9_]{1,160}',code):code='MEDIA_PDS_HTTP_'+str(error.code)
   raise ValueError(code)from None
  if len(data)>64*1024*1024:raise ValueError('MEDIA_PDS_RESPONSE_LIMIT')
  return data if raw else json.loads(data)
 def receipt(self,claim):
  return self.call('/v1/media-artifacts/receipts/'+urllib.parse.quote(claim.request_id,safe='')+'/'+urllib.parse.quote(claim.attempt_id,safe=''),missing=True)
 def publish(self,packet):return self.call('/v1/media-artifacts/publish',packet)
 def asset(self,asset_id):
  value=self.call('/v1/media-artifacts/assets/'+urllib.parse.quote(asset_id,safe=''))
  raw=base64.b64decode(value['base64'],validate=True)
  if value.get('assetId')!=asset_id or value.get('bytes')!=len(raw) or value.get('sha256')!=hashlib.sha256(raw).hexdigest():raise ValueError('MEDIA_ASSET_ENVELOPE_INVALID')
  return raw

class ImageWorkspace:
 """source(claim,folder) is a trusted PDS source reader/compiler, not user code.
 It returns {revision,projectId,targetId,execution,inputFiles:[{ref,path,sha256,bytes}]}.
 Cleanup is an ownership-aware PDS scratch callback, not recursive deletion here.
 """
 capability_prefix='image.';mime='image/png';model='codex.image_gen'
 def __init__(self,root,client,source,cleanup):
  self.root=Path(root)
  if not self.root.is_absolute()or self.root.resolve(strict=True)!=self.root or not self.root.is_dir():raise ValueError('MEDIA_SCRATCH_ROOT_INVALID')
  self.client,self.source,self.cleanup_port=client,source,cleanup
 def folder(self,claim):return self.root/digest([claim.request_id,claim.attempt_id])
 def stage(self,claim):
  if not claim.capability.startswith(self.capability_prefix):raise ValueError('MEDIA_CAPABILITY_INVALID')
  folder=self.folder(claim)
  if folder.exists()or folder.is_symlink():raise ReconciliationRequired('MEDIA_ATTEMPT_ALREADY_STAGED')
  folder.mkdir(mode=0o700)
  write_new(folder/'claim.json',json.dumps(asdict(claim),sort_keys=True).encode())
  pin=self.source(claim,folder)
  if str(pin['revision'])!=claim.expected_revision:raise ValueError('MEDIA_SOURCE_REVISION_CHANGED')
  rows=[]
  for row in pin['inputFiles']:
   name=row['path']
   if Path(name).name!=name or name in ('claim.json','execution.json','source.json'):raise ValueError('MEDIA_INPUT_PATH_INVALID')
   data=row['bytes']
   if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('MEDIA_INPUT_HASH_MISMATCH')
   write_new(folder/name,data);rows.append({k:row[k]for k in ('ref','path','sha256')})
  packet={'schemaVersion':'workers.execution.v1','requestId':claim.request_id,'attemptId':claim.attempt_id,'capability':claim.capability,'expectedRevision':claim.expected_revision,'requestHash':digest(dict(claim.parameters)),'inputRefs':list(claim.input_refs),'inputFiles':rows,'execution':pin['execution']}
  write_new(folder/'execution.json',json.dumps(packet).encode())
  write_new(folder/'source.json',json.dumps({k:pin[k]for k in ('revision','projectId','targetId')}).encode())
  validate_stage(claim,folder);return folder
 def publish(self,claim,workspace,artifacts):
  if Path(workspace)!=self.folder(claim):raise ValueError('MEDIA_WORKSPACE_MISMATCH')
  validate_stage(claim,workspace)
  if len(artifacts)!=2:raise ValueError('MEDIA_ARTIFACT_COUNT_INVALID')
  path=Path(artifacts[0]);path=safe_file(workspace,str(path.relative_to(workspace)));data=path.read_bytes();sha=hashlib.sha256(data).hexdigest()
  source=json.loads(safe_file(workspace,'source.json').read_text())
  packet={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'capability':claim.capability,'projectId':source['projectId'],'targetId':source['targetId'],'expectedRevision':claim.expected_revision,'requestHash':digest(dict(claim.parameters)),'artifact':{'sha256':sha,'bytes':len(data),'mimeType':self.mime,'base64':base64.b64encode(data).decode()},'provenance':{'model':self.model or claim.parameters['model']},'claim':asdict(claim)}
  self._intent(claim,workspace,packet)
  receipt=self.client.publish(packet)
  if receipt!=self.client.receipt(claim):raise ValueError('MEDIA_RECEIPT_READBACK_MISMATCH')
  return self._verified(claim,packet,receipt)
 def _verified(self,claim,packet,receipt):
  data=base64.b64decode(packet['artifact']['base64'],validate=True)
  if len(data)!=packet['artifact']['bytes']or hashlib.sha256(data).hexdigest()!=packet['artifact']['sha256']:raise ValueError('MEDIA_INTENT_BYTES_INVALID')
  expected={k:v for k,v in packet.items()if k not in ('artifact','provenance','claim')}
  if any(receipt.get(k)!=v for k,v in expected.items())or receipt.get('verified')is not True or receipt.get('authority')!='filex'or receipt.get('status')!='committed':raise ValueError('MEDIA_RECEIPT_BINDING_MISMATCH')
  artifact=receipt['artifact']
  if any(artifact.get(k)!=packet['artifact'][k]for k in ('sha256','bytes','mimeType'))or self.client.asset(artifact['assetId'])!=data:raise ValueError('MEDIA_ASSET_READBACK_MISMATCH')
  if receipt.get('publicationHash')!=digest(packet)or receipt.get('receiptHash')!=digest({k:v for k,v in receipt.items()if k!='receiptHash'}):raise ValueError('MEDIA_RECEIPT_HASH_INVALID')
  return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,(artifact['assetId'],),receipt['receiptHash'],True)
 def _intent(self,claim,workspace,packet):
  if digest(json.loads(safe_file(workspace,'claim.json').read_text()))!=digest(asdict(claim)):raise ValueError('MEDIA_RECOVERY_OWNER_MISMATCH')
  path=Path(workspace)/'publication-intent.json'
  if path.exists()or path.is_symlink():
   if digest(json.loads(safe_file(workspace,'publication-intent.json').read_text()))!=digest(packet):raise ValueError('MEDIA_PUBLICATION_INTENT_CHANGED')
  else:
   write_new(path,json.dumps(packet,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())
   fd=os.open(str(workspace),os.O_RDONLY|os.O_DIRECTORY)
   try:os.fsync(fd)
   finally:os.close(fd)
 def _recovery_packet(self,claim):
  folder=self.folder(claim)
  if not folder.exists():return None
  if digest(json.loads(safe_file(folder,'claim.json').read_text()))!=digest(asdict(claim)):raise ValueError('MEDIA_RECOVERY_OWNER_MISMATCH')
  path=folder/'publication-intent.json'
  if not path.exists()and not path.is_symlink():return None
  packet=json.loads(safe_file(folder,'publication-intent.json').read_text());source=json.loads(safe_file(folder,'source.json').read_text())
  expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'capability':claim.capability,'expectedRevision':claim.expected_revision,'requestHash':digest(dict(claim.parameters)),'projectId':source['projectId'],'targetId':source['targetId']}
  if digest(packet.get('claim'))!=digest(asdict(claim))or any(packet.get(k)!=v for k,v in expected.items())or packet.get('provenance')!={'model':self.model or claim.parameters['model']}or packet.get('artifact',{}).get('mimeType')!=self.mime:raise ValueError('MEDIA_RECOVERY_INTENT_SCOPE')
  return packet
 def recover(self,claim):
  # Service-only inspector. No generation, upload, re-post or cleanup here.
  packet=self._recovery_packet(claim)
  if packet is None:return None
  receipt=self.client.receipt(claim)
  if receipt is None:return None
  return self._verified(claim,packet,receipt)
 def resume_publication(self,claim,expected_publication_hash):
  # Service-only: caller must prove existing reservation and stopped execution.
  # Never accepts a new payload; the exact durable intent is the only input.
  packet=self._recovery_packet(claim)
  if packet is None:raise ValueError('MEDIA_RESUME_INTENT_REQUIRED')
  if not isinstance(expected_publication_hash,str)or not re.fullmatch('[a-f0-9]{64}',expected_publication_hash)or digest(packet)!=expected_publication_hash:raise ValueError('MEDIA_RESUME_HASH_MISMATCH')
  data=base64.b64decode(packet['artifact']['base64'],validate=True)
  if len(data)!=packet['artifact']['bytes']or hashlib.sha256(data).hexdigest()!=packet['artifact']['sha256']:raise ValueError('MEDIA_INTENT_BYTES_INVALID')
  receipt=self.client.publish(packet)
  recovered=self.recover(claim)
  if recovered is None or receipt!=self.client.receipt(claim):raise ValueError('MEDIA_RESUME_READBACK_MISMATCH')
  return recovered
 def cleanup(self,claim,workspace):
  if workspace is not None and Path(workspace)!=self.folder(claim):raise ValueError('MEDIA_CLEANUP_SCOPE_INVALID')
  return self.cleanup_port(claim,self.folder(claim))is True

class PortraitSource:
 """Reuse an already compiled official portrait record; verify live PDS context.

 context_reader(record) must perform the existing guarded PDS context read.
 asset_reader(assetId) reads reference bytes through PDS, never local old paths.
 No prompt generation or rewrite is done by this source adapter.
 """
 def __init__(self,context_reader,asset_reader):self.context_reader,self.asset_reader=context_reader,asset_reader
 def __call__(self,claim,folder):
  import copy
  record=claim.parameters;context=record['context'];native=copy.deepcopy(record['claim'])
  if record.get('schemaVersion')!='npc-workflow-portrait-single.v1'or 'renderingOverride'in context or 'renderingConflictFields'in context:raise ValueError('IMAGE_OFFICIAL_RECORD_REQUIRED')
  current=self.context_reader(record)
  for key in ('npcRevision','characterHash','incarnationId','sourceVersion'):
   if current.get(key)!=context.get(key):raise ValueError('IMAGE_CONTEXT_CHANGED')
  if str(context['npcRevision'])!=claim.expected_revision:raise ValueError('IMAGE_REVISION_CHANGED')
  if current.get('categories')!=context.get('categories'):raise ValueError('IMAGE_CONTEXT_CATEGORIES_CHANGED')
  for row in current['categories']:
   if row.get('contentHash')!=digest(row['value']):raise ValueError('IMAGE_CATEGORY_HASH_INVALID')
  provider=native['providerRequest']
  if native.get('providerRequestHash')!=digest(provider):raise ValueError('IMAGE_PINNED_PROVIDER_CHANGED')
  references=native.get('referenceManifest',{}).get('references',[]);files=[]
  if provider['arguments'].get('referenced_image_paths',[])!=[r['localPath']for r in references]:raise ValueError('IMAGE_REFERENCE_MANIFEST_REQUIRED')
  if len(references)!=len(claim.input_refs):raise ValueError('IMAGE_REFERENCE_COUNT_MISMATCH')
  for ref,pds_ref in zip(references,claim.input_refs):
   asset=ref['mainPortraitAssetId']
   if asset!=ref['sha256']+'.png':raise ValueError('IMAGE_REFERENCE_ASSET_INVALID')
   data=self.asset_reader(asset)
   if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('IMAGE_REFERENCE_HASH_CHANGED')
   files.append({'ref':pds_ref,'path':asset,'sha256':ref['sha256'],'bytes':data});ref['localPath']=str(folder/asset)
  if references:
   manifest=native['referenceManifest'];manifest['manifestHash']=digest({k:v for k,v in manifest.items()if k!='manifestHash'})
   provider['metadata']['referenceManifest']=copy.deepcopy(manifest)
   provider['arguments']['referenced_image_paths']=[r['localPath']for r in references]
   native['providerRequestHash']=digest(provider)
  target=record['request'].get('character',record['request'].get('characterRef',{}))['npcId']
  return {'revision':context['npcRevision'],'projectId':'binari','targetId':target,'execution':{'providerClaim':native},'inputFiles':files}

class PortraitContextReader:
 """Existing guarded PDS read API via AnimationPublicationClient-compatible port."""
 def __init__(self,client):self.client=client
 def __call__(self,record):
  context=record['context'];request=record['request'];npc_id=request.get('character',request.get('characterRef',{}))['npcId']
  snapshot=self.client.json('/api/npcs')
  rows=[n for n in snapshot['document']['characters']if n['id']==npc_id]
  if len(rows)!=1:raise ValueError('IMAGE_NPC_NOT_FOUND')
  npc=rows[0]
  if str(snapshot['revision'])!=str(context['npcRevision'])or digest(npc)!=context['characterHash']:raise ValueError('IMAGE_SOURCE_CHANGED')
  value=self.client.json('/api/worker-context/read',{'projectId':'binari','npcId':npc_id,'npcRevision':snapshot['revision'],'sourceVersion':context['sourceVersion'],'categories':[c['id']for c in context['categories']],**({'posePreset':record['posePreset']}if record.get('posePreset')else{})})
  if value.get('schemaVersion')!='pds-worker-context-read.v2':raise ValueError('IMAGE_PDS_CONTEXT_SCHEMA')
  return {'npcRevision':value['npcRevision'],'characterHash':value['characterHash'],'sourceVersion':value['sourceVersion'],'incarnationId':npc.get('creationBasis',{}).get('requestId'),'categories':value['categories']}

def media_publication_verifier(client):
 """Request-service callback: re-read PDS, never trust worker receipt alone."""
 def verify(claim,receipt):
  receipt.validate(claim);raw=client.receipt(claim)
  if (raw.get('requestId')!=claim.request_id or raw.get('attemptId')!=claim.attempt_id or raw.get('fencingToken')!=claim.fencing_token or raw.get('requestHash')!=digest(dict(claim.parameters)) or raw.get('verified')is not True or raw.get('authority')!='filex' or raw.get('status')!='committed' or raw.get('receiptHash')!=digest({k:v for k,v in raw.items()if k!='receiptHash'}) or receipt.revision!=raw['receiptHash'] or receipt.asset_refs!=(raw['artifact']['assetId'],)):
   raise ValueError('MEDIA_COMPLETION_RECEIPT_INVALID')
  data=client.asset(raw['artifact']['assetId'])
  if hashlib.sha256(data).hexdigest()!=raw['artifact']['sha256']or len(data)!=raw['artifact']['bytes']:raise ValueError('MEDIA_COMPLETION_ASSET_INVALID')
  return {'sourceReceipt':{'requestId':claim.request_id,'revision':raw['receiptHash']},'publication':{'requestId':claim.request_id,'verified':True},'reservedReceipt':raw}
 return verify
