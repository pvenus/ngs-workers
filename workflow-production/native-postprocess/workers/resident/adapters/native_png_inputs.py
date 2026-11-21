"""Opt-in frozen PDS input identity, no paths, writes, claims, or inference.

Only existing authenticated client read methods are consumed. Authority scope
and generation are supplied by the caller's already verified admission.
"""
import base64,hashlib,json,re
from .animation_pose import binding
from .animation_context import read_context
from .animation_workspace import context_basis,input_basis
from .frame_workspace import digest
SCHEMA='ngs.animation-native-inputs.v1'
def require(ok):
 if not ok:raise ValueError('ANIMATION_NATIVE_INPUT_CHANGED')
def freeze_inputs(client,request,*,generation_id,read_generation):
 required={'schemaVersion','projectId','npcId','posePreset','presetId','direction','referenceType','intent'}
 require(type(request)is dict and set(request)-{'seed'}==required and request['schemaVersion']=='ngs.npc-animation-request.v1' and request['projectId']=='binari' and request['referenceType']=='pixel')
 require(all(type(request[k])is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',request[k])for k in ('npcId','presetId')))
 require(request['posePreset']in ('representative','idle','combat') and request['direction']in ('n','ne','e','se','s','sw','w','nw'))
 generation=read_generation(request['npcId']);require(generation['generationId']==generation_id and generation['npcId']==request['npcId'] and generation['state']=='active')
 current=client.current();matches=[n for n in current['document']['characters']if n['id']==request['npcId']];require(len(matches)==1);npc=matches[0]
 pose=binding(npc,request);context=read_context(npc,request,lambda method,path,payload=None:client.json(path,payload))
 motion=client.json('/v1/motion-presets/'+request['presetId']+'/'+request['direction'])
 require(all(motion.get(k)==request[k]for k in ('posePreset','presetId','direction')) and motion.get('cycleCount')==1)
 raw=base64.b64decode(motion['base64'],validate=True);sha=hashlib.sha256(raw).hexdigest();require(motion['sha256']==sha and motion['assetId']==sha+'.mp4' and motion['bytes']==len(raw))
 require(type(motion['frameCount'])is int and 2<=motion['frameCount']<=1000 and type(motion['fps'])in (int,float) and 0<motion['fps']<=120)
 reference=client.asset(pose['referenceAssetId']);require(hashlib.sha256(reference).hexdigest()+'.png'==pose['referenceAssetId'])
 # Full current context was already checked. Existing semantic projections keep
 # unrelated output slots from changing the frozen generation inputs.
 body={'schemaVersion':SCHEMA,'npcId':request['npcId'],'generationId':generation_id,'requestHash':digest(request),'pose':pose,'npcInputHash':input_basis(npc,request),'contextHash':digest(context_basis(context)),'reference':{'assetId':pose['referenceAssetId'],'sha256':hashlib.sha256(reference).hexdigest(),'bytes':len(reference)},'motion':{'assetId':motion['assetId'],'sha256':sha,'bytes':len(raw),'presetId':request['presetId'],'direction':request['direction'],'frameCount':motion['frameCount'],'fps':motion['fps'],'policyHash':digest(motion['policy'])}}
 require(read_generation(request['npcId'])==generation)
 # No encoded bytes or local filenames escape this immutable identity object.
 return json.loads(json.dumps(body)),{'reference':reference,'motion':raw}
def read_frozen_inputs(client,request,frozen,*,read_generation):
 body,payloads=freeze_inputs(client,request,generation_id=frozen['generationId'],read_generation=read_generation)
 require(body==frozen)
 return payloads

def read_claim_inputs(client,claim,*,read_generation,assert_current):
 """Opt-in post-claim read, before model load; no cached admission authority.

 assert_current is the existing Request lease/fence check, not a local boolean
 stored at selection. It is called around PDS reads. The backend must still use
 its existing publication guard at write time; this is input validation only.
 """
 from ..contracts import PROFILES
 claim.validate(PROFILES['animation'])
 task=claim.parameters
 require(task.get('schemaVersion')=='ngs.animation-native-sequence-direction.v1'
         and task.get('requestId')==claim.request_id)
 frozen=task.get('frozenInputs');request=task.get('directionRequest')
 require(type(frozen)is dict and type(request)is dict
         and digest(frozen)==task.get('sourceBindingHash')==claim.expected_revision
         and all(task.get(k)==request.get(k)for k in ('npcId','posePreset','presetId','direction'))
         and frozen.get('generationId')==task.get('generationId'))
 require(claim.input_refs==(frozen['reference']['assetId'],frozen['motion']['assetId']))
 require(assert_current(claim)is True)
 payloads=read_frozen_inputs(client,request,frozen,read_generation=read_generation)
 require(assert_current(claim)is True)
 return payloads

def verify_frozen_for_selection(client,request,frozen,*,read_generation):
 """Definitive mismatch only after a complete, successful authority re-read.

 Transport errors, incomplete/invalid replies and changing generation fail the
 read normally. They are NOT evidence for the accepted-row failed transition.
 """
 observed,_=freeze_inputs(client,request,generation_id=frozen['generationId'],read_generation=read_generation)
 if observed!=frozen:
  from local.services.animation_requests.native_png_workflow import FrozenInputStale
  raise FrozenInputStale(observed)
 return True

def stage_raw_inputs(client,claim,*,read_generation,assert_current):
 """Build the original Wan job from verified current/frozen PDS inputs only."""
 from .animation_context import prompt_values
 from .animation_workspace import DIRECTION_VIEWS
 payloads=read_claim_inputs(client,claim,read_generation=read_generation,assert_current=assert_current)
 task=claim.parameters;request=task['directionRequest'];frozen=task['frozenInputs']
 current=client.current();matches=[n for n in current['document']['characters']if n['id']==task['npcId']];require(len(matches)==1)
 npc=matches[0];context=read_context(npc,request,lambda method,path,payload=None:client.json(path,payload))
 motion=client.json('/v1/motion-presets/'+task['presetId']+'/'+task['direction'])
 require(digest(context_basis(context))==frozen['contextHash']and input_basis(npc,request)==frozen['npcInputHash']
         and digest(motion['policy'])==frozen['motion']['policyHash']and motion['sha256']==frozen['motion']['sha256'])
 values=prompt_values(context,request);policy=motion['policy']
 job=dict(frameCount=frozen['motion']['frameCount'],fps=frozen['motion']['fps'],referenceType='pixel',framingPolicy=policy['framingPolicy'],
  appearancePrompt='Preserve supplied pixel image identity, outfit, proportions, rendering and facing direction, not static pose. Fixed full-body camera. Appearance: '+json.dumps(values,ensure_ascii=False)+' Equipment: '+json.dumps(policy['equipmentPolicy'],ensure_ascii=False),
  negativePrompt='multiple cycles, repeated action, text, watermark, extra limbs, changing identity, camera motion',
  motionPrompt='Follow motion reference facing '+request['direction']+': '+DIRECTION_VIEWS[request['direction']]+'. Maintain facing throughout; do not turn toward camera. '+policy['motionInstruction']+' Request: '+request['intent'])
 if 'seed'in request:job['seed']=request['seed']
 require(read_claim_inputs(client,claim,read_generation=read_generation,assert_current=assert_current)==payloads)
 return dict(job=job,**payloads)
