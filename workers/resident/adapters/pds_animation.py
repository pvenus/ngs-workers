"""Existing PDS animation API with guarded immutable receipt verification."""
import hashlib,json,re,urllib.request
from .pds_frames import FramePublicationClient
from .pds_category_files import encode,header
class AnimationPublicationClient(FramePublicationClient):
 def json(self,path,packet=None):
  if not (path in ('/api/npcs','/api/worker-context/read') or path.startswith('/api/worker-context/catalog?') or re.fullmatch(r'/v1/motion-presets/[A-Za-z0-9._:-]+/(n|ne|e|se|s|sw|w|nw)',path)):raise ValueError('ANIMATION_PDS_PATH_DENIED')
  return self._frame_call(path,packet)
 def receipt(self,request_id):
  if not re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',request_id):raise ValueError('ANIMATION_REQUEST_ID_INVALID')
  value=self._frame_call('/v1/npcs/animations/requests/'+request_id)
  if value is not None and (value.get('requestId')!=request_id or value.get('verified')is not True or value.get('source')!='filex'):raise ValueError('ANIMATION_RECEIPT_UNVERIFIED')
  return value
 def verify(self,packet,receipt):
  artifact=packet['artifact'];expected=hashlib.sha256(encode({**packet,'artifact':{k:v for k,v in artifact.items()if k!='base64'}})).hexdigest()
  fields={'requestId':packet['requestId'],'inputHash':expected,'npcId':packet['npcId'],'slotKey':'/'.join(packet['slot'][k]for k in ('posePreset','presetId','direction')),'assetId':artifact['sha256']+'.mp4','sha256':artifact['sha256'],'bytes':artifact['bytes'],'mimeType':'video/mp4','verified':True,'source':'filex'}
  if not isinstance(receipt,dict)or any(receipt.get(k)!=v for k,v in fields.items()):raise ValueError('ANIMATION_RECEIPT_BINDING_MISMATCH')
  return receipt
 def publish(self,packet):
  if packet.get('npcId')!=self._guard()['npcId']or packet.get('requestId')!=self.stage:raise ValueError('ANIMATION_PUBLISH_SCOPE_MISMATCH')
  old=self.receipt(packet['requestId'])
  if old is not None:return self.verify(packet,old)
  result=self._frame_call('/v1/npcs/animations/publish',packet);self.verify(packet,result)
  if self.receipt(packet['requestId'])!=result:raise ValueError('ANIMATION_RECEIPT_READBACK_MISMATCH')
  return result
