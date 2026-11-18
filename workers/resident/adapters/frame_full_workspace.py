"""Full frame requests: only current PDS video bytes, never old frame reuse."""
import base64,hashlib,json,os,re,urllib.request
from dataclasses import asdict
from pathlib import Path
from ..contracts import Receipt
from ..runtime import ReconciliationRequired
from ..backends.frame_postprocess import DIRECTIONS
from ..backends.image_music_workspace import safe_file,write_new
from .frame_workspace import HalfFrameWorkspace,digest,save as _save
from .pds_frames import FramePublicationClient
from .pds_category_files import header

def save(path,value):
 path=Path(path)
 if path.exists()or path.is_symlink():
  if digest(json.loads(safe_file(path.parent,path.name).read_text()))!=digest(value):raise ValueError('FRAME_IMMUTABLE_RECORD_CHANGED')
 else:
  _save(path,value)
  fd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)

class FullFrameClient(FramePublicationClient):
 def video(self,asset):
  if not re.fullmatch('[a-f0-9]{64}\\.mp4',asset):raise ValueError('FRAME_VIDEO_ASSET_INVALID')
  req=urllib.request.Request(self.base+'/assets/animations/'+asset,headers={'X-NPC-Ownership-Guard':header(self._guard()),'X-NPC-Stage-Request-Id':self.stage})
  with self.opener.open(req,timeout=self.timeout)as r:data=r.read(256*1024*1024+1)
  if len(data)>256*1024*1024 or hashlib.sha256(data).hexdigest()+'.mp4'!=asset:raise ValueError('FRAME_VIDEO_HASH_MISMATCH')
  return data

class FullFrameWorkspace(HalfFrameWorkspace):
 def _sources(self,npc,request):
  result={}
  for direction in DIRECTIONS:
   row=npc.get('animationSlots',{}).get('/'.join((request['posePreset'],request['presetId'],direction)))
   if not isinstance(row,dict)or not re.fullmatch('[a-f0-9]{64}\\.mp4',str(row.get('assetId',''))):raise ValueError('FRAMES_EIGHT_SOURCE_VIDEOS_REQUIRED')
   result[direction]=row
  return result
 def stage(self,claim):
  request=claim.parameters
  if (set(request)-{'scalePreset'}!={'schemaVersion','projectId','npcId','posePreset','presetId','intent'}or request.get('schemaVersion')!='ngs.npc-animation-frames-request.v1'or request.get('projectId')!='binari'or request.get('scalePreset','full')!='full'or request.get('posePreset')not in ('representative','idle','combat')or not all(isinstance(request.get(k),str)and re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',request[k])for k in ('npcId','presetId'))or not isinstance(request.get('intent'),str)or not 1<=len(request['intent'].strip())<=4000):raise ValueError('FRAME_FULL_REQUEST_REQUIRED')
  folder=self._folder(claim)
  if folder.exists()or folder.is_symlink():raise ReconciliationRequired('EXISTING_ATTEMPT_REQUIRES_RECONCILIATION')
  folder.mkdir(mode=0o700);save(folder/'claim.json',asdict(claim))
  current=self.client.current();npc=self._npc(current,request)
  if str(current['revision'])!=claim.expected_revision:raise ValueError('FRAME_STAGE_REVISION_CHANGED')
  sources=self._sources(npc,request);key=request['posePreset']+'/'+request['presetId']
  expected=['pds:asset:'+sources[d]['assetId']for d in DIRECTIONS]
  if tuple(claim.input_refs)!=tuple(expected):raise ValueError('FRAME_VIDEO_INPUT_REFS_CHANGED')
  save(folder/'pin.json',{'creationBasis':npc.get('creationBasis'),'target':npc.get('animationFrameSets',{}).get(key),'sources':sources})
  rows={}
  for d,row in sources.items():
   data=self.client.video(row['assetId']);name=d+'.mp4';write_new(folder/name,data)
   rows[d]={'path':name,'sourceAssetId':row['assetId'],'sha256':row['assetId'][:-4]}
  save(folder/'full-frame-inputs.json',{'requestId':claim.request_id,'attemptId':claim.attempt_id,'expectedRevision':claim.expected_revision,'directions':rows})
  return folder
 def publish(self,claim,workspace,artifacts):
  if Path(workspace)!=self._folder(claim):raise ValueError('FRAME_WORKSPACE_MISMATCH')
  manifest=json.loads(safe_file(workspace,'frame-manifest.json').read_text());request=claim.parameters
  if manifest.get('requestId')!=claim.request_id or manifest.get('canvas',{}).get('scale')!=.5:raise ValueError('FRAME_FULL_PIXEL_STAGE_REQUIRED')
  envelopes=[]
  for sha,name in manifest.pop('artifacts').items():
   raw=safe_file(workspace,str(Path(name).relative_to(workspace))).read_bytes()
   if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('FRAME_OUTPUT_HASH_INVALID')
   envelopes.append({'sha256':sha,'bytes':len(raw),'mimeType':'image/png','base64':base64.b64encode(raw).decode()})
  current=self.client.current();npc=self._npc(current,request);pin=json.loads(safe_file(workspace,'pin.json').read_text());key=request['posePreset']+'/'+request['presetId']
  if self._sources(npc,request)!=pin['sources']or npc.get('creationBasis')!=pin['creationBasis']or npc.get('animationFrameSets',{}).get(key)!=pin['target']:raise ValueError('FRAME_SOURCE_OR_TARGET_CHANGED')
  manifest.update(posePreset=request['posePreset'],presetId=request['presetId'],scalePreset='full',pixelScale=.5,processingPolicy='extract-alpha-pixel-half.v1')
  packet={'requestId':claim.request_id,'projectId':request['projectId'],'npcId':request['npcId'],'expectedSourceVersion':current['sourceVersion'],'expectedCharacterHash':digest(npc),'manifest':manifest,'artifacts':envelopes,'trace':{'sourceSlots':pin['sources'],'sourceMapHash':digest(pin['sources']),'modelCalls':0,'inputTransport':'PDS','processing':'all frames; model alpha; mandatory half-scale shared-palette pixels'}}
  save(workspace/'publication-intent.json',packet);receipt=self.client.publish(packet);save(workspace/'publication-receipt.json',receipt)
  return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,tuple(a['sha256']+'.png'for a in envelopes),str(receipt['revision']),True)
