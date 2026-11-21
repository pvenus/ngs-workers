"""Request-owned pose slot PDS transport. No generation or direct Filex access."""
import re
from urllib.parse import urlencode
from .pds_animation import AnimationPublicationClient
from ..deployment.request_selector import digest

class PosePublicationClient(AnimationPublicationClient):
 def api(self,method,path,payload=None,raw=False):
  if raw:
   if method!='GET' or not path.startswith('/assets/portraits/'):raise ValueError('POSE_PDS_RAW_SCOPE')
   return self.asset(path.rsplit('/',1)[1])
  allowed=(method=='GET' and (path in ('/api/npcs','/v1/npcs/files') or re.fullmatch(r'/v1/npcs/pose-views/requests/[A-Za-z0-9._:-]+',path))) or (method=='POST' and path=='/v1/npcs/pose-views/publish')
  if not allowed:raise ValueError('POSE_PDS_PATH_DENIED')
  return self._frame_call(path,payload)
 def receipt(self,rid):
  if not re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',rid):raise ValueError('POSE_REQUEST_ID_INVALID')
  value=self.api('GET','/v1/npcs/pose-views/requests/'+rid)
  if value is not None and (value.get('requestId')!=rid or value.get('verified')is not True or value.get('source')!='filex'):raise ValueError('POSE_RECEIPT_UNVERIFIED')
  return value
 def context(self,npc,incarnation,*,posePreset):
  current=self.json('/api/npcs')
  rows=[v for v in current['document']['characters'] if v['id']==npc]
  if len(rows)!=1 or rows[0].get('creationBasis',{}).get('requestId')!=incarnation:raise ValueError('POSE_INCARNATION_CHANGED')
  catalog=self.json('/api/worker-context/catalog?'+urlencode({'projectId':'binari','npcId':npc}))
  value=self.json('/api/worker-context/read',{'projectId':'binari','npcId':npc,'npcRevision':catalog['npcRevision'],'sourceVersion':catalog['sourceVersion'],'posePreset':posePreset,'categories':['common.portrait-rendering','unique.npc-identity','unique.npc-design']})
  if value.get('schemaVersion')!='pds-worker-context-read.v2' or value.get('characterHash')!=digest(rows[0]):raise ValueError('POSE_CONTEXT_CHANGED')
  return {**value,'incarnationId':incarnation}
 def verify(self,packet,receipt):
  import hashlib
  expected=digest({**{k:v for k,v in packet.items() if k not in ('expectedSourceVersion','expectedCharacterHash')},'artifact':{k:v for k,v in packet['artifact'].items() if k!='base64'},'sourceArtifact':{k:v for k,v in packet['sourceArtifact'].items() if k!='base64'}})
  slot=packet['slot'];artifact=packet['artifact']
  fields={'requestId':packet['requestId'],'inputHash':expected,'npcId':packet['npcId'],'slotKey':'/'.join(slot[k]for k in ('posePreset','referenceType','direction')),'assetId':artifact['sha256']+'.png','sourceArtifactId':packet['sourceArtifact']['sha256']+'.png','sha256':artifact['sha256'],'bytes':artifact['bytes'],'verified':True,'source':'filex'}
  if not isinstance(receipt,dict) or any(receipt.get(k)!=v for k,v in fields.items()):raise ValueError('POSE_PUBLICATION_RECEIPT_CHANGED')
  return receipt
