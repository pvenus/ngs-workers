"""Direct pair bytes to PDS. No Request byte relay or credential discovery.

The existing configured PDS ingress credential must already authorize this port.
A live metadata reservation is validated by PDS, not asserted by this client.
"""
import base64,hashlib,json,struct,urllib.request,urllib.parse
from dataclasses import asdict
from pathlib import Path
from .image_pds import MediaArtifactClient
from ..backends.image_music_workspace import safe_file,digest

JSON_FILES=('provider-proof.json','pixel-claim.json','pixel-result.json')
PNG_FILES=('output.png','pixel-output.png','pixel-source.png')
MAX_METADATA=2*1024*1024
MAX_PNG=32*1024*1024

def encode_pair(claim,workspace,packet):
 root=Path(workspace)
 if not root.is_absolute() or root.resolve()!=root:raise ValueError('PAIR_WORKSPACE_REQUIRED')
 if set(packet)!={'manifest'}:raise ValueError('PAIR_PACKET_INVALID')
 manifest=packet['manifest']
 expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'originalRecordHash':digest(dict(claim.parameters)),'schemaVersion':'resident.portrait-pair.v1'}
 if any(manifest.get(k)!=v for k,v in expected.items()) or set(manifest.get('files',{}))!=set(JSON_FILES+PNG_FILES):raise ValueError('PAIR_MANIFEST_SCOPE')
 if digest(json.loads(safe_file(root,'claim.json').read_text()))!=digest(asdict(claim)):raise ValueError('PAIR_OWNER_MISMATCH')
 if json.loads(safe_file(root,'artifacts/pair-manifest.json').read_text())!=manifest:raise ValueError('PAIR_MANIFEST_CHANGED')
 data={}
 for name in JSON_FILES+PNG_FILES:
  path=safe_file(root,'artifacts/'+name);limit=MAX_PNG if name in PNG_FILES else MAX_METADATA
  if not 0<path.stat().st_size<=limit:raise ValueError('PAIR_FILE_LIMIT')
  raw=path.read_bytes()
  if manifest['files'][name]!={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}:raise ValueError('PAIR_FILE_HASH_CHANGED')
  if name in PNG_FILES and not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('PAIR_PNG_REQUIRED')
  data[name]=raw
 metadata={'claim':asdict(claim),'packet':packet,'metadataFiles':{name:data[name].decode('utf-8')for name in JSON_FILES}}
 raw=json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
 if len(raw)>MAX_METADATA:raise ValueError('PAIR_METADATA_LIMIT')
 return struct.pack('>I',len(raw))+raw+b''.join(data[name]for name in PNG_FILES)

class DirectPortraitPairClient(MediaArtifactClient):
 def __init__(self,base_url,token=None,*,timeout=30,ownership_guard=None):
  if ownership_guard is None:
   super().__init__(base_url,token,timeout=timeout);self.ownership_guard=None;return
  if token is not None or not callable(ownership_guard):raise ValueError('DIRECT_PDS_AUTH_MODE_INVALID')
  p=urllib.parse.urlsplit(base_url)
  if p.scheme!='http'or p.hostname!='127.0.0.1'or not p.port or p.path not in ('','/')or p.query or p.fragment or p.username or p.password:raise ValueError('MEDIA_PDS_LOOPBACK_REQUIRED')
  from .pds_category_files import NoRedirect
  self.base=base_url.rstrip('/');self.token=None;self.timeout=timeout;self.ownership_guard=ownership_guard
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def _post(self,path,raw,mime,claim=None):
  headers={'Content-Type':mime}
  if self.ownership_guard is None:headers['Authorization']='Bearer '+self.token
  else:
   if claim is None:raise ValueError('DIRECT_PDS_CLAIM_REQUIRED')
   guard=self.ownership_guard(claim)
   if not isinstance(guard,dict)or set(guard)!={'npcId','generationId','requestId','leaseToken'}or any(not isinstance(v,str)or not v for v in guard.values()):raise ValueError('DIRECT_PDS_GUARD_INVALID')
   headers.update({'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(guard,sort_keys=True,separators=(',',':')).encode()).decode(),'X-NPC-Stage-Request-Id':claim.request_id})
  request=urllib.request.Request(self.base+path,data=raw,headers=headers)
  with self.opener.open(request,timeout=self.timeout)as response:
   result=response.read(MAX_METADATA+1)
   if response.status!=200 or len(result)>MAX_METADATA:raise ValueError('PAIR_PDS_RESPONSE_INVALID')
  value=json.loads(result)
  if not isinstance(value,dict):raise ValueError('PAIR_PDS_RESPONSE_INVALID')
  return value
 def read(self,claim,packet_hash):
  return self._post('/v1/npc-portrait-pairs/read',json.dumps({'claim':asdict(claim),'packetHash':packet_hash}).encode(),'application/json',claim)
 def publish(self,claim,workspace,packet,reservation):
  # Reservation data is not a capability supplied by the worker. The PDS
  # independently resolves exact live claim+packetHash from Request authority.
  if not isinstance(reservation,dict):raise ValueError('PAIR_RESERVATION_REQUIRED')
  raw=encode_pair(claim,workspace,packet)
  value=self._post('/v1/npc-portrait-pairs/publish',raw,'application/octet-stream',claim)
  if set(value)!={'requestId','attemptId','fencingToken','packetHash','parentReceipt','canonicalReceipt'} or any(value.get(k)!=v for k,v in {'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'packetHash':digest(packet)}.items()):raise ValueError('PAIR_PDS_RECEIPT_SCOPE')
  parent=value['parentReceipt'];canonical=value['canonicalReceipt']
  if not isinstance(parent,dict) or parent.get('sourceApplied') is not True or parent.get('publicationScope')!='registered-npc-portrait-pair' or parent.get('publication',{}).get('verified') is not True or not isinstance(canonical,dict) or canonical.get('status')!='committed':raise ValueError('PAIR_PDS_RECEIPT_UNVERIFIED')
  if self.read(claim,digest(packet))!=value:raise ValueError('PAIR_PDS_READBACK_CHANGED')
  return value
