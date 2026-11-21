"""Request-service PDS canonical byte registrar. No local FileService writes."""
import base64,hashlib,json,re,urllib.request
from urllib.parse import urlsplit,quote
from .pds_category_files import NoRedirect
from ..deployment.request_selector import digest

class PortraitAssetClient:
 def __init__(self,base,token,guard,stage_request_id,*,timeout=30):
  p=urlsplit(base)
  if p.scheme!='http' or p.hostname!='127.0.0.1' or not p.port or p.path not in ('','/') or p.query or p.fragment or p.username or p.password:raise ValueError('PAIR_PDS_LOOPBACK_REQUIRED')
  if not isinstance(token,str) or len(token)<32 or '\r'in token or '\n'in token or not callable(guard) or not isinstance(stage_request_id,str) or not stage_request_id:raise ValueError('PAIR_PDS_SERVICE_BINDING_REQUIRED')
  if not 0<timeout<=30:raise ValueError('PAIR_PDS_TIMEOUT')
  self.base,self.token,self.guard,self.stage,self.timeout=base.rstrip('/'),token,guard,stage_request_id,timeout
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def _call(self,path,raw=None,metadata=None,binary=False):
  headers={'Authorization':'Bearer '+self.token}
  if raw is not None:
   guard=self.guard()
   if not isinstance(guard,dict) or any(not guard.get(k) for k in ('npcId','generationId','requestId','leaseToken')):raise ValueError('PAIR_PDS_GUARD_REQUIRED')
   encoded=base64.b64encode(json.dumps(metadata,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).decode()
   if len(encoded)>60000:raise ValueError('PAIR_PDS_METADATA_LIMIT')
   headers.update({'Content-Type':'image/png','X-Portrait-Metadata':encoded,'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(guard).encode()).decode(),'X-NPC-Stage-Request-Id':self.stage})
  req=urllib.request.Request(self.base+path,data=raw,headers=headers)
  with self.opener.open(req,timeout=self.timeout) as response:data=response.read(32*1024*1024+1)
  if len(data)>32*1024*1024:raise ValueError('PAIR_PDS_RESPONSE_LIMIT')
  return data if binary else json.loads(data)
 def register(self,raw,project,run_id,provenance):
  if project!='binari' or run_id not in (self.stage,self.stage+'.pixel') or not isinstance(raw,bytes) or not 0<len(raw)<=32*1024*1024:raise ValueError('PAIR_PDS_ASSET_SCOPE')
  metadata={'runId':run_id,'projectId':'ProjectBS','sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'provenance':provenance}
  receipt=self._call('/v1/portrait-assets/register',raw,metadata)
  asset=self._check(receipt)
  guard=self.guard();binding={'metadata':metadata,'npcId':guard['npcId'],'generationId':guard['generationId'],'parentRequestId':guard['requestId'],'stageRequestId':self.stage}
  if receipt.get('inputHash')!=digest(binding) or any(receipt.get(k)!=v for k,v in binding.items()if k!='metadata'):raise ValueError('PAIR_PDS_REGISTER_SCOPE_CHANGED')
  expected='asset.'+hashlib.sha256(('binari:'+run_id).encode()).hexdigest()[:48]
  if asset['id']!=expected or asset['sha256']!=metadata['sha256'] or asset['size']!=len(raw) or asset.get('provenance')!=provenance:raise ValueError('PAIR_PDS_REGISTER_CHANGED')
  if self._call('/v1/portrait-assets/'+quote(expected,safe=''))!=receipt or self._call('/v1/portrait-assets/'+quote(expected,safe='')+'/content',binary=True)!=raw:raise ValueError('PAIR_PDS_REGISTER_READBACK_CHANGED')
  return asset
 def _check(self,receipt):
  if receipt.get('schemaVersion')!='pds.portrait-asset.v1' or receipt.get('verified') is not True or receipt.get('authority')!='filex' or receipt.get('receiptHash')!=digest({k:v for k,v in receipt.items()if k!='receiptHash'}):raise ValueError('PAIR_PDS_ASSET_RECEIPT_INVALID')
  asset=receipt['asset']
  if any(asset.get(k)!=v for k,v in {'projectId':'ProjectBS','kind':'portrait','mimeType':'image/png','status':'available','canonicalAssetId':asset.get('sha256','')+'.png'}.items()) or not re.fullmatch(r'asset\.[a-f0-9]{48}',asset.get('id','')):raise ValueError('PAIR_PDS_ASSET_INVALID')
  return asset
 def verify(self,asset,output):
  receipt=self._call('/v1/portrait-assets/'+quote(asset['id'],safe=''));current=self._check(receipt)
  raw=self._call('/v1/portrait-assets/'+quote(asset['id'],safe='')+'/content',binary=True)
  if current!=asset or current['sha256']!=output['sha256'] or current['size']!=output['size'] or len(raw)!=output['size'] or hashlib.sha256(raw).hexdigest()!=output['sha256']:raise ValueError('PAIR_PDS_ASSET_READBACK_CHANGED')
  return True
 def publication_receipt(self,commit_id):
  if not isinstance(commit_id,str) or not re.fullmatch(r'npc\.portrait\.[a-f0-9]{64}',commit_id):raise ValueError('PAIR_PDS_COMMIT_ID')
  return self._call('/v1/npc-portrait-pairs/receipts/'+commit_id)
