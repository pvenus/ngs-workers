"""Single-direction official animation stage; PDS inputs and CAS publication only."""
import base64,hashlib,json,re
from dataclasses import asdict
from pathlib import Path
from ..contracts import Receipt,PROFILES
from ..runtime import ReconciliationRequired
from .frame_workspace import HalfFrameWorkspace,digest,save
from .animation_pose import binding
from .animation_context import read_context,prompt_values
DIRECTION_VIEWS={'n':'rear view, facing away from the camera','ne':'rear three-quarter view, facing screen-right and away','e':'right side profile, facing screen-right','se':'front three-quarter view, facing screen-right and toward camera','s':'front view, facing toward the camera','sw':'front three-quarter view, facing screen-left and toward camera','w':'left side profile, facing screen-left','nw':'rear three-quarter view, facing screen-left and away'}

def input_basis(npc,request):
 key='/'.join(request[k]for k in ('posePreset','presetId','direction'))
 return digest({**{k:v for k,v in npc.items()if k not in ('animationSlots','animationFrameSets')},'targetSlot':npc.get('animationSlots',{}).get(key)})

def context_basis(context):
 return {k:v for k,v in context.items()if k not in ('npcRevision','characterHash','sourceVersion')}

class AnimationWorkspace(HalfFrameWorkspace):
 def _context(self,npc,request):return read_context(npc,request,lambda method,path,payload=None:self.client.json(path,payload))
 def stage(self,claim):
  claim.validate(PROFILES['animation']);request=dict(claim.parameters)
  required={'schemaVersion','projectId','npcId','posePreset','presetId','direction','referenceType','intent'}
  if set(request)!=required or request['schemaVersion']!='ngs.npc-animation-request.v1' or request['projectId']!='binari' or not isinstance(request['intent'],str)or not 1<=len(request['intent'])<=12000 or request['referenceType']!='pixel' or request['direction']not in ('n','ne','e','se','s','sw','w','nw')or request['posePreset']not in ('representative','idle','combat')or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',request['presetId']):raise ValueError('ANIMATION_SINGLE_DIRECTION_REQUEST_REQUIRED')
  folder=self._folder(claim)
  if folder.exists()or folder.is_symlink():raise ReconciliationRequired('EXISTING_ATTEMPT_REQUIRES_RECONCILIATION')
  folder.mkdir(mode=0o700);save(folder/'claim.json',asdict(claim))
  current=self.client.current();npc=self._npc(current,request)
  if str(current['revision'])!=claim.expected_revision:raise ValueError('ANIMATION_STAGE_REVISION_CHANGED')
  pose=binding(npc,request);context=self._context(npc,request);values=prompt_values(context,request)
  motion=self.client.json('/v1/motion-presets/'+request['presetId']+'/'+request['direction'])
  if any(motion.get(k)!=request[k]for k in ('posePreset','presetId','direction'))or motion.get('cycleCount')!=1:raise ValueError('ANIMATION_MOTION_CHANGED')
  raw=base64.b64decode(motion['base64'],validate=True)
  if hashlib.sha256(raw).hexdigest()!=motion['sha256']or len(raw)!=motion['bytes']or motion['assetId']!=motion['sha256']+'.mp4':raise ValueError('ANIMATION_MOTION_HASH')
  ref='pds:asset:'+pose['referenceAssetId'];mref='pds:motion:'+motion['sha256']
  if set(claim.input_refs)!={ref,mref}:raise ValueError('ANIMATION_INPUT_REFS_CHANGED')
  reference=self.client.asset(pose['referenceAssetId']);(folder/'reference.png').write_bytes(reference);(folder/'motion.mp4').write_bytes(raw)
  policy=motion['policy'];job={'frameCount':motion['frameCount'],'fps':motion['fps'],'referenceType':'pixel','framingPolicy':policy['framingPolicy'],'appearancePrompt':'Preserve supplied pixel image identity, outfit, proportions, rendering and facing direction, not static pose. Fixed full-body camera. Appearance: '+json.dumps(values,ensure_ascii=False)+' Equipment: '+json.dumps(policy['equipmentPolicy'],ensure_ascii=False),'negativePrompt':'multiple cycles, repeated action, text, watermark, extra limbs, changing identity, camera motion','motionPrompt':'Follow motion reference facing '+request['direction']+': '+DIRECTION_VIEWS[request['direction']]+'. Maintain facing throughout; do not turn toward camera. '+policy['motionInstruction']+' Request: '+request['intent']}
  save(folder/'pin.json',{'sourceVersion':current['sourceVersion'],'characterHash':digest(npc),'inputBasis':input_basis(npc,request),'context':context,'poseReference':pose,'motion':{k:v for k,v in motion.items()if k!='base64'}})
  save(folder/'animation-job.json',{'claim':{'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'expectedRevision':claim.expected_revision,'parametersHash':digest(request),'inputRefs':list(claim.input_refs)},'job':job,'inputs':{'reference':{'path':'reference.png','sha256':hashlib.sha256(reference).hexdigest(),'ref':ref},'motion':{'path':'motion.mp4','sha256':motion['sha256'],'ref':mref}}})
  return folder
 def publish(self,claim,workspace,artifacts):
  if workspace!=self._folder(claim)or len(artifacts)!=1:raise ValueError('ANIMATION_WORKSPACE_MISMATCH')
  path=Path(artifacts[0]);resolved=path.resolve(strict=True)
  if path.is_symlink()or not resolved.is_relative_to(workspace):raise ValueError('ANIMATION_ARTIFACT_PATH')
  completed=json.loads((workspace/'wan'/'completed.json').read_text())
  if Path(completed.get('path','')).resolve()!=resolved:raise ValueError('ANIMATION_COMPLETION_PATH')
  data=resolved.read_bytes()
  if completed.get('sha256')!=hashlib.sha256(data).hexdigest()or completed.get('bytes')!=len(data):raise ValueError('ANIMATION_COMPLETION_HASH')
  if len(data)<=12 or data[4:8]!=b'ftyp':raise ValueError('ANIMATION_MP4_INVALID')
  pin=json.loads((workspace/'pin.json').read_text());request=dict(claim.parameters);motion=pin['motion'];current=self.client.current();npc=self._npc(current,request)
  if input_basis(npc,request)!=pin['inputBasis']or context_basis(self._context(npc,request))!=context_basis(pin['context'])or binding(npc,request)!=pin['poseReference']:raise ValueError('ANIMATION_PUBLICATION_SOURCE_CHANGED')
  latest_motion=self.client.json('/v1/motion-presets/'+request['presetId']+'/'+request['direction'])
  if {k:v for k,v in latest_motion.items()if k!='base64'}!=motion:raise ValueError('ANIMATION_PUBLICATION_MOTION_CHANGED')
  if completed.get('frameCount')!=motion['frameCount']or completed.get('fps')!=motion['fps']:raise ValueError('ANIMATION_COMPLETION_TIMING')
  slot={k:request[k]for k in ('posePreset','presetId','direction','referenceType')};slot.update(referenceAssetId=pin['poseReference']['referenceAssetId'],frameCount=motion['frameCount'],fps=motion['fps'],cycleCount=1,presetHash=motion['sha256'])
  sha=hashlib.sha256(data).hexdigest();packet={'requestId':claim.request_id,'projectId':'binari','npcId':request['npcId'],'expectedSourceVersion':current['sourceVersion'],'expectedCharacterHash':digest(npc),'slot':slot,'artifact':{'sha256':sha,'bytes':len(data),'mimeType':'video/mp4','base64':base64.b64encode(data).decode()},'trace':{'poseReference':pin['poseReference'],'context':pin['context']['trace'],'worker':'resident-animation','visualCycleVerified':False,'generation':completed,'actualPrompt':{k:v for k,v in json.loads((workspace/'animation-job.json').read_text())['job'].items()if k.endswith('Prompt')}}}
  save(workspace/'publication-intent.json',packet);receipt=self.client.publish(packet);save(workspace/'publication-receipt.json',receipt)
  return Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,(receipt['assetId'],),str(receipt['revision']),True)
