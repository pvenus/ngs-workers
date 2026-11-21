"""Concrete Request-service domain readers; never imported in a worker.

Snapshot provider must be the admitted PDS authority. Existing DTOs are immutable;
current snapshots become separate claim bindings, not injected DTO fields.
"""
import json
from .request_selector import FormalPayloadSelector,digest

ANIMATION=('npc.animation.generate','ngs.animation-intake.v1','animation.requests')
FRAMES=('npc.animation.frames.generate','ngs.frame-intake.v1','frames.requests')
IMAGE=('npc.workflow.stage','npc-workflow-portrait-single.v1','npc.workflow.portrait')
IMAGE_SINGLE=('npc.workflow.stage','npc-workflow-portrait-single.v1','npc.single.portrait')
DOCUMENT=('npc.workflow','ngs.npc-category-candidate.v1','npc.category-candidate')
MUSIC=('audio.music.generate','audio-music-hq.v1','audio.music')

class DomainResolvers:
 def __init__(self,common,snapshot,*,portrait_record=None,document_bridge=None,music_intake=None):
  self.common,self.snapshot=common,snapshot
  self.portrait_record,self.document_bridge,self.music_intake=portrait_record,document_bridge,music_intake
 def _media(self,row,ref,table):
  record=self.common.db.execute('SELECT actor,input_hash,body FROM '+table+' WHERE id=?',(ref['reference_id'],)).fetchone()
  if record is None or record['actor']!=row['requester']:raise ValueError('FORMAL_DOMAIN_OWNER_MISMATCH')
  payload=json.loads(record['body'])
  if digest(payload)!=record['input_hash'] or record['input_hash']!=ref['input_hash']:raise ValueError('FORMAL_DOMAIN_HASH_CHANGED')
  pin=self.snapshot(row,payload)
  if (not isinstance(pin,dict) or pin.get('projectId')!=row['project_id']or pin.get('targetId')!=payload['npcId']
      or not isinstance(pin.get('expectedRevision'),str)or not pin['expectedRevision']):raise ValueError('FORMAL_PDS_SNAPSHOT_REQUIRED')
  return {'requestId':row['request_id'],'parameters':payload,'inputRefs':pin['inputRefs'],'expectedRevision':pin['expectedRevision'],
          'binding':{k:pin[k]for k in ('projectId','targetId','expectedRevision')}}
 def animation(self,row,ref):return self._media(row,ref,'animation_intake_requests')
 def frames(self,row,ref):return self._media(row,ref,'frame_intake_requests')
 def image(self,row,ref):
  if self.portrait_record is None:raise ValueError('FORMAL_PORTRAIT_READER_REQUIRED')
  record=self.portrait_record(ref['reference_id'])
  if digest(record)!=ref['input_hash'] or record.get('actor')!=row['requester']:raise ValueError('FORMAL_PORTRAIT_BINDING_INVALID')
  req=record['request'];char=req.get('characterRef',req.get('character',{}));target=char.get('npcId',char.get('id'))
  if not isinstance(target,str)or not target:raise ValueError('FORMAL_PORTRAIT_TARGET_REQUIRED')
  revision=str(record['context']['npcRevision'])
  manifest=record['claim'].get('referenceManifest')
  refs=[]
  if manifest is not None:
   import re
   if not isinstance(manifest,dict) or manifest.get('manifestHash')!=digest({k:v for k,v in manifest.items() if k!='manifestHash'}) or not isinstance(manifest.get('references'),list):raise ValueError('FORMAL_PORTRAIT_REFERENCES_INVALID')
   for item in manifest['references']:
    sha=item.get('sha256');aid=item.get('mainPortraitAssetId')
    if not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha) or aid!=sha+'.png' or item.get('npcId')!=target:raise ValueError('FORMAL_PORTRAIT_REFERENCES_INVALID')
    refs.append('pds:asset:'+aid)
  return {'requestId':row['request_id'],'parameters':record,'inputRefs':refs,
          'expectedRevision':revision,'binding':{'projectId':row['project_id'],'targetId':target,'expectedRevision':revision}}
 def document(self,row,ref):
  if self.document_bridge is None:raise ValueError('FORMAL_PARENT_BRIDGE_REQUIRED')
  return self.document_bridge.payload(row,ref)
 def music(self,row,ref):
  if self.music_intake is None:raise ValueError('FORMAL_MUSIC_INTAKE_REQUIRED')
  return self.music_intake.payload(row['request_id'])
 def selector(self,validators,ready,enabled):
  supported={ANIMATION:('animation.generate',self.animation),FRAMES:('frames.extract',self.frames),
             IMAGE:('image.generate',self.image),IMAGE_SINGLE:('image.generate',self.image),DOCUMENT:('document.edit',self.document),MUSIC:('music.generate',self.music)}
  routes={}
  for route in enabled:
   if route not in supported or route not in validators or not callable(validators[route]):raise ValueError('FORMAL_ROUTE_VALIDATOR_REQUIRED')
   cap,resolver=supported[route];routes[route]=(cap,resolver,validators[route])
  return FormalPayloadSelector(self.common,routes,ready=ready)

def validate_document_envelope(value):
 if not isinstance(value,dict)or set(value)!={'variation','stage','request','childKey','actor','parentId'}:raise ValueError('DOCUMENT_ENVELOPE_INVALID')
 if (not isinstance(value['stage'],dict)or value['stage'].get('candidateCount')!=1
     or value['stage'].get('id')not in ('overview','art','category-add','category-modify')):raise ValueError('DOCUMENT_ENVELOPE_STAGE_INVALID')
 r=value['request']
 if (not isinstance(r,dict)or r.get('schemaVersion')!='npc-category-request.v1' or r.get('projectId')!='binari'
     or r.get('mode')not in ('add','modify')or r.get('candidateCount',1)!=1
     or type(r.get('baseRevision'))is not int or r['baseRevision']<1
     or any(not isinstance(r.get(k),str)or not r[k]for k in ('npcId','intent'))):raise ValueError('DOCUMENT_NPC_REQUEST_INVALID')
 if r['mode']=='modify'and(not isinstance(r.get('categoryId'),str)or not r['categoryId']):raise ValueError('DOCUMENT_NPC_TARGET_REQUIRED')
 return value
