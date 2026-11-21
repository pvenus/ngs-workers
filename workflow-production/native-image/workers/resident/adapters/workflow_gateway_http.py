"""Service-authenticated ID-only workflow gateway; no owner credential forwarding."""
import hmac,json,re,urllib.request,math
from .pds_category_files import NoRedirect
from http.server import BaseHTTPRequestHandler

def handler(gateway,identities):
 identities=dict(identities)
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def reply(self,status,value):
   raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   if self.client_address[0]!='127.0.0.1'or self.headers.get('Origin')is not None or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return self.reply(403,{'error':'LOOPBACK_ONLY'})
   auth=self.headers.get('Authorization','');token=auth[7:]if auth.startswith('Bearer ')else ''
   owner=next((v for k,v in identities.items()if token and hmac.compare_digest(k,token)),None)
   if owner is None:return self.reply(401,{'error':'WORKFLOW_SERVICE_AUTH_REQUIRED'})
   length=self.headers.get('Content-Length','')
   if self.headers.get('Content-Type')!='application/json'or not length.isdecimal()or not 0<int(length)<=65536:return self.reply(400,{'error':'WORKFLOW_BODY_INVALID'})
   self.connection.settimeout(5)
   try:
    body=json.loads(self.rfile.read(int(length)));op=self.path.removeprefix('/v1/workflow-gateway/')
    required=set()if op=='runs'else{'parentRequestId','definitionHash','requestHash'}|({'stageId'}if op in ('submit','view')else set())
    if self.path!='/v1/workflow-gateway/'+op or op not in ('runs','read','submit','view')or not isinstance(body,dict)or set(body)!=required:raise ValueError('WORKFLOW_COMMAND_INVALID')
    if op=='runs':result=gateway.runs(owner)
    else:
     parent=gateway.read(body['parentRequestId'],owner)
     if body['definitionHash']!=parent['planHash']or body['requestHash']!=parent['requestHash']:raise ValueError('WORKFLOW_AUTHORIZED_RUN_CHANGED')
     result=parent if op=='read'else getattr(gateway,op)(body['parentRequestId'],body['stageId'],owner)
    self.reply(200,result)
   except (ValueError,KeyError,TypeError)as error:
    code=str(error);self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'WORKFLOW_CONTRACT_ERROR'})
 return Handler

class HttpWorkflowGateway:
 def __init__(self,base,token,*,timeout=15):
  from urllib.parse import urlsplit
  p=urlsplit(base)
  if p.scheme!='http'or p.hostname!='127.0.0.1'or not p.port or p.path or p.query or p.fragment or p.username or p.password:raise ValueError('WORKFLOW_LOOPBACK_URL_REQUIRED')
  if not isinstance(token,str)or len(token)<32:raise ValueError('WORKFLOW_SERVICE_TOKEN_REQUIRED')
  if isinstance(timeout,bool)or not isinstance(timeout,(int,float))or not math.isfinite(timeout)or not 0<timeout<=120:raise ValueError('WORKFLOW_TIMEOUT_INVALID')
  self.base,self.token,self.timeout=base,token,timeout
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def _call(self,operation,body):
  req=urllib.request.Request(self.base+'/v1/workflow-gateway/'+operation,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
  with self.opener.open(req,timeout=self.timeout)as response:
   data=response.read(4*1024*1024+1)
   if len(data)>4*1024*1024:raise ValueError('WORKFLOW_RESPONSE_TOO_LARGE')
   return json.loads(data)
 def runs(self):return self._call('runs',{})
 def read(self,binding):return self._call('read',dict(binding))
 def submit(self,binding,stage):return self._call('submit',{**binding,'stageId':stage})
 def view(self,binding,stage):return self._call('view',{**binding,'stageId':stage})
