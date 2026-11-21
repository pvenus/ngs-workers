"""Authenticated pose direction publication port, hosted in the Request service only."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac,json,re,urllib.request
from .request_http import HttpRequests
from ..contracts import Claim,PROFILES

class HttpPoseDirections(HttpRequests):
 def _draft(self,operation,claim,packet=None):
  body={'claim':asdict(claim)}
  if packet is not None:body['packet']=packet
  request=urllib.request.Request(self.base+'/v1/pose-directions/'+operation,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
  with self.opener.open(request,timeout=120 if operation in ('submit','finish') else 30 if operation in ('authorize','source') else self.timeout)as response:
   raw=response.read(1024*1024+1)
   if len(raw)>1024*1024:raise ValueError('POSE_RESPONSE_TOO_LARGE')
   return json.loads(raw)
 def authorize(self,claim):return self._draft('authorize',claim)
 def submit(self,claim,packet):return self._draft('submit',claim,packet)
 def read(self,claim):return self._draft('read',claim)
 def source(self,claim):return self._draft('source',claim)
 def prepare(self,claim,packet):return self._draft('prepare',claim,packet)
 def finish(self,claim,packet):return self._draft('finish',claim,packet)

def handler(bridge,identities,verify_worker):
 identities=dict(identities)
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def reply(self,status,value):
   raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   if self.client_address[0]!='127.0.0.1'or self.headers.get('Origin')is not None or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return self.reply(403,{'error':'LOOPBACK_ONLY'})
   auth=self.headers.get('Authorization','');token=auth[7:]if auth.startswith('Bearer ')else ''
   owner=next((v for k,v in identities.items()if token and hmac.compare_digest(k,token)),None)
   if owner is None:return self.reply(401,{'error':'WORKER_AUTH_REQUIRED'})
   length=self.headers.get('Content-Length','')
   if self.headers.get('Content-Type')!='application/json'or not length.isdecimal()or not 0<int(length)<=1024*1024:return self.reply(400,{'error':'BODY_INVALID'})
   self.connection.settimeout(5)
   try:
    body=json.loads(self.rfile.read(int(length)));op=self.path.removeprefix('/v1/pose-directions/')
    if self.path!='/v1/pose-directions/'+op or op not in ('authorize','submit','read','source','prepare','finish')or set(body)!=({'claim','packet'}if op in ('submit','prepare','finish')else{'claim'}):raise ValueError('POSE_COMMAND_INVALID')
    raw=body['claim'];raw['input_refs']=tuple(raw['input_refs']);claim=Claim(**raw);claim.validate(PROFILES['image'])
    if verify_worker(claim,owner)is not True:raise ValueError('POSE_WORKER_SCOPE')
    bridge.authorize(claim)
    if op=='authorize':result=bridge.authorize(claim)
    elif op=='read':result=bridge.read(claim)
    elif op=='source':result=bridge.source(claim)
    elif op in ('prepare','finish'):
     if not callable(getattr(bridge,op,None)):raise ValueError('POSE_DIRECT_MODE_REQUIRED')
     result=getattr(bridge,op)(claim,body['packet'])
    else:bridge.submit(claim,body['packet']);result={'accepted':True}
    self.reply(200,result)
   except (ValueError,KeyError,TypeError)as e:
    code=str(e);self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'POSE_CONTRACT_ERROR'})
 return Handler
