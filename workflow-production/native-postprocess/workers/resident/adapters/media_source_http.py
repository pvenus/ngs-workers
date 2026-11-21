"""Music-only metadata read port. No file bytes or worker-selected paths.

Worker client imports no Request authority/SQLite implementation. The host
instantiates handler with its own exact issued-claim ledger and domain reader.
"""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac,json,re,urllib.request,urllib.error
from .request_http import HttpRequests
from ..contracts import Claim,PROFILES

MAX_BODY=1024*1024
MAX_RESPONSE=65536
PATH='/v1/media-source/read'

def metadata(value,claim):
 if not isinstance(value,dict)or set(value)!={'revision','projectId','targetId','execution','inputFiles'}:raise ValueError('MUSIC_SOURCE_SHAPE')
 if value['execution']!={}or value['inputFiles']!=[]:raise ValueError('MUSIC_SOURCE_FILES_FORBIDDEN')
 if any(not isinstance(value[k],str)or not value[k]or len(value[k])>1024 for k in ('revision','projectId','targetId'))or value['revision']!=claim.expected_revision:raise ValueError('MUSIC_SOURCE_BINDING')
 return value

class HttpMediaSource(HttpRequests):
 def __call__(self,claim,folder=None):
  claim.validate(PROFILES['music'])
  if claim.capability!='music.generate':raise ValueError('MUSIC_SOURCE_CAPABILITY')
  raw=json.dumps({'claim':asdict(claim)},separators=(',',':'),allow_nan=False).encode()
  if len(raw)>MAX_BODY:raise ValueError('MUSIC_SOURCE_BODY_TOO_LARGE')
  request=urllib.request.Request(self.base+PATH,data=raw,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
  try:
   with self.opener.open(request,timeout=self.timeout)as response:
    raw=response.read(MAX_RESPONSE+1)
    if len(raw)>MAX_RESPONSE:raise ValueError('MUSIC_SOURCE_RESPONSE_TOO_LARGE')
    return metadata(json.loads(raw),claim)
  except urllib.error.HTTPError as exc:raise RuntimeError('MUSIC_SOURCE_HTTP_'+str(exc.code))from None

def handler(bridge,identities,source_reader):
 identities=dict(identities)
 if not callable(source_reader)or any(not token or not isinstance(v,(tuple,list))or len(v)!=2 or not v[0]or v[1]not in PROFILES for token,v in identities.items()):raise ValueError('MUSIC_SOURCE_CONFIG')
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def reply(self,status,value):
   raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()
   if len(raw)>MAX_RESPONSE:status,raw=409,b'{"error":"MUSIC_SOURCE_RESPONSE_TOO_LARGE"}'
   self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   if self.client_address[0]!='127.0.0.1'or self.headers.get('Origin')is not None or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return self.reply(403,{'error':'LOOPBACK_ONLY'})
   auth=self.headers.get('Authorization','');token=auth[7:]if auth.startswith('Bearer ')else ''
   owner=next((v for k,v in identities.items()if token and hmac.compare_digest(k,token)),None)
   if owner is None:return self.reply(401,{'error':'WORKER_AUTH_REQUIRED'})
   if owner[1]!='music':return self.reply(403,{'error':'MUSIC_WORKER_REQUIRED'})
   length=self.headers.get('Content-Length','')
   if self.headers.get('Transfer-Encoding')is not None or self.headers.get('Content-Type')!='application/json'or not length.isdecimal()or not 0<int(length)<=MAX_BODY:return self.reply(400,{'error':'BODY_INVALID'})
   self.connection.settimeout(5)
   try:
    body=json.loads(self.rfile.read(int(length)))
    if self.path!=PATH or not isinstance(body,dict)or set(body)!={'claim'}:raise ValueError('MUSIC_SOURCE_COMMAND')
    raw=body['claim'];raw['input_refs']=tuple(raw['input_refs']);claim=Claim(**raw);claim.validate(PROFILES['music'])
    if claim.capability!='music.generate':raise ValueError('MUSIC_SOURCE_CAPABILITY')
    def authorize():
     with bridge.transaction():
      lease=bridge._lease(claim)
      if lease['workerId']!=owner[0]:raise ValueError('MUSIC_SOURCE_WORKER_SCOPE')
      bridge.authorize_publication(claim)
      row=bridge.common.db.execute('SELECT binding_json FROM resident_claim_bindings WHERE grant_id=?',(claim.resource_grant,)).fetchone()
      if row is None:raise ValueError('MUSIC_SOURCE_ISSUED_BINDING_REQUIRED')
      return json.loads(row[0])
    binding=authorize()
    value=metadata(source_reader(claim),claim)
    if binding!=authorize()or any(value[k]!=binding[b]for k,b in (('revision','expectedRevision'),('projectId','projectId'),('targetId','targetId'))):raise ValueError('MUSIC_SOURCE_BINDING')
    self.reply(200,value)
   except (ValueError,KeyError,TypeError)as exc:
    code=str(exc);self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'MUSIC_SOURCE_CONTRACT'})
 return Handler
