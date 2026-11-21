"""Service-token-only operator endpoint; no worker/user supplied stop booleans."""
import hmac,json,re,subprocess
from http.server import BaseHTTPRequestHandler
from ..contracts import Claim,PROFILES
PREFIX='/v1/host-boundary-recovery/'
def handler(authority,token):
 if not isinstance(token,str)or len(token)<32:raise ValueError('HOST_RECOVERY_TOKEN')
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def reply(self,status,value):
   raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   if self.client_address[0]!='127.0.0.1'or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port)or self.headers.get('Origin')is not None:return self.reply(403,{'error':'HOST_RECOVERY_LOCAL_ONLY'})
   if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.reply(403,{'error':'HOST_RECOVERY_SERVICE_AUTH'})
   op=self.path.removeprefix(PREFIX)
   if self.path!=PREFIX+op or op not in ('arm','confirm','finalize','status'):return self.reply(404,{'error':'NOT_FOUND'})
   try:
    n=self.headers.get('Content-Length','');require=n.isdecimal()and 0<int(n)<=1024*1024 and self.headers.get('Content-Type')=='application/json'and not self.headers.get('Transfer-Encoding')
    if not require:raise ValueError('HOST_RECOVERY_BODY')
    self.connection.settimeout(5);raw=self.rfile.read(int(n))
    if len(raw)!=int(n):raise ValueError('HOST_RECOVERY_BODY')
    body=json.loads(raw)
    if set(body)!={'claim'}:raise ValueError('HOST_RECOVERY_COMMAND')
    value=body['claim'];value['input_refs']=tuple(value['input_refs']);c=Claim(**value);c.validate(PROFILES['document'])
    if op=='status':
     with authority.bridge.transaction():r=authority._read(c)
     result={'requestId':c.request_id,'grantId':c.resource_grant,'state':r['state'],'scopeHash':r['scopeHash'],'executionStopped':authority.verify_stopped(c)}
    else:result=getattr(authority,{'arm':'arm','confirm':'confirm','finalize':'finalize_failed'}[op])(c)
    return self.reply(200,result)
   except (OSError,subprocess.SubprocessError):return self.reply(503,{'error':'HOST_RECOVERY_OBSERVATION_UNAVAILABLE'})
   except (ValueError,TypeError,KeyError)as exc:
    code=str(exc);return self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'HOST_RECOVERY_INVALID'})
 return Handler
