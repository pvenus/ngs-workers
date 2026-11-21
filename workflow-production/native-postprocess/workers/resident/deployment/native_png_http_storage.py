"""Worker data-plane client; no Filex credentials or Request byte transport.

The selected host supplies the existing original ownership-guard resolver.
Current ingress is same-Mac only; this client does not imply remote deployment
support or create authorization grants.
"""
import base64,hashlib,json,struct,urllib.request,urllib.parse
from dataclasses import asdict

def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()
def asset_ids(value):
 result=set()
 def visit(v):
  if isinstance(v,dict):
   for k,x in v.items():
    if k in ('assetId','alphaAssetId')and isinstance(x,str):result.add(x)
    visit(x)
  elif isinstance(v,list):
   for x in v:visit(x)
 visit(value);return sorted(result)

class NativePNGHTTPStorage:
 def __init__(self,endpoint,guard_for):
  url=urllib.parse.urlsplit(endpoint)
  if url.scheme!='http'or url.hostname!='127.0.0.1'or not url.port or url.username or url.password or url.path not in ('','/')or url.query or url.fragment or not callable(guard_for):raise ValueError('OWNED_HTTP_LOCAL_ENDPOINT_REQUIRED')
  self.endpoint=endpoint.rstrip('/');self.guard_for=guard_for
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*a,**kw):raise ValueError('OWNED_HTTP_REDIRECT_DENIED')
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def _call(self,claim,action,metadata,payload=b''):
  guard=self.guard_for(claim)
  if type(guard)is not dict or set(guard)!={'npcId','generationId','requestId','leaseToken'}:raise ValueError('OWNED_HTTP_ORIGINAL_GUARD_REQUIRED')
  meta=encoded({'claim':asdict(claim),'action':action,'metadata':metadata})
  if len(meta)>1024*1024:raise ValueError('OWNED_HTTP_BODY_LIMIT')
  req=urllib.request.Request(self.endpoint+'/v1/npcs/ownership/operations',data=struct.pack('>I',len(meta))+meta+payload,method='POST',headers={'Content-Type':'application/octet-stream','X-NPC-Ownership-Guard':base64.b64encode(encoded(guard)).decode(),'X-NPC-Stage-Request-Id':claim.request_id})
  with self.opener.open(req,timeout=120)as r:
   if r.status!=200 or r.headers.get_content_type()!='application/json':raise ValueError('OWNED_HTTP_RESPONSE')
   length=r.headers.get('Content-Length','')
   if not length.isdecimal():raise ValueError('OWNED_HTTP_RESPONSE_LENGTH')
   raw=r.read(int(length)+1)
   if len(raw)!=int(length):raise ValueError('OWNED_HTTP_RESPONSE_LENGTH')
  value=json.loads(raw)
  if type(value)is not dict or set(value)!={'result'}:raise ValueError('OWNED_HTTP_RESPONSE')
  result=value['result']
  if action in ('commit','receipt') and result is not None:
   if type(result)is not dict or result.get('generationId')!=guard['generationId'] or result.get('parentRequestId')!=guard['requestId'] or result.get('stageRequestId')!=claim.request_id or result.get('inputHash')!=claim.expected_revision:raise ValueError('OWNED_HTTP_RECEIPT_SCOPE')
  return result
 def upload_asset(self,claim,asset_id,data):
  if type(data)is not bytes or asset_id!=hashlib.sha256(data).hexdigest()+'.png':raise ValueError('OWNED_HTTP_ASSET_HASH')
  result=self._call(claim,'asset',{'assetId':asset_id,'sha256':asset_id[:-4],'bytes':len(data)},data)
  if type(result)is not dict or result.get('sha256')!=asset_id[:-4]:raise ValueError('OWNED_HTTP_ASSET_RECEIPT')
  return result
 def commit(self,claim,input_hash,files,read_set):
  if type(files)is not list or len(files)!=1 or type(files[0])is not dict:raise ValueError('OWNED_HTTP_BATCH')
  f=files[0];body={'inputHash':input_hash,'files':files,'readSet':read_set}
  result=self._call(claim,'commit',{'path':f['path'],'inputHash':input_hash,'manifestHash':digest(f['body']),'assetIds':asset_ids(f['body'])},encoded(body))
  if type(result)is not dict or result.get('requestId')!=claim.request_id or result.get('status')!='committed' or result.get('inputHash')!=input_hash or result.get('hashes')!={f['path']:digest(f['body'])}:raise ValueError('OWNED_HTTP_COMMIT_RECEIPT')
  return result
 def receipt(self,claim):
  result=self._call(claim,'receipt',{})
  if result is not None and (type(result)is not dict or result.get('requestId')!=claim.request_id or result.get('status')!='committed'):raise ValueError('OWNED_HTTP_READ_RECEIPT')
  return result
