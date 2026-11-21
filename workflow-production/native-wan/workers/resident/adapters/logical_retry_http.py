"""Service-only IDs, no caller-supplied guard, proof, actor or stop boolean."""
import hmac,json,re
from http.server import BaseHTTPRequestHandler

def handler(authority,*,closure_token,retry_token,parent_actor):
 if not callable(parent_actor)or any(not isinstance(t,str)or len(t)<32 for t in (closure_token,retry_token))or closure_token==retry_token:raise ValueError('LOGICAL_RETRY_SERVICE_IDENTITIES')
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def reply(self,n,value):
   raw=json.dumps(value).encode();self.send_response(n);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   if self.client_address[0]!='127.0.0.1'or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port)or self.headers.get('Origin')is not None:return self.reply(403,{'error':'LOGICAL_LOCAL_ONLY'})
   if self.path=='/v1/native-publication-closures/read':token=closure_token;keys={'parentRequestId','closureFenceId'}
   elif self.path=='/v1/logical-timeout-retry/proof':token=retry_token;keys={'parentRequestId'}
   else:return self.reply(404,{'error':'NOT_FOUND'})
   if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.reply(403,{'error':'LOGICAL_SERVICE_AUTH'})
   try:
    n=self.headers.get('Content-Length','')
    if not n.isdecimal()or not 0<int(n)<=16384 or self.headers.get('Content-Type')!='application/json'or self.headers.get('Transfer-Encoding'):raise ValueError('LOGICAL_BODY_INVALID')
    self.connection.settimeout(5);raw=self.rfile.read(int(n))
    if len(raw)!=int(n):raise ValueError('LOGICAL_BODY_INVALID')
    v=json.loads(raw)
    if not isinstance(v,dict)or set(v)!=keys or any(not isinstance(x,str)or not re.fullmatch('[a-zA-Z0-9_.:-]{1,200}',x)for x in v.values()):raise ValueError('LOGICAL_COMMAND_INVALID')
    if 'closureFenceId'in v:result=authority.read_closure(v['parentRequestId'],v['closureFenceId'])
    else:result=authority.proof(v['parentRequestId'],parent_actor(v['parentRequestId']))
    return self.reply(200,result)
   except (OSError,TimeoutError):return self.reply(503,{'error':'LOGICAL_AUTHORITY_UNAVAILABLE'})
   except (ValueError,KeyError,TypeError)as e:
    code=str(e);return self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'LOGICAL_CONTRACT_INVALID'})
 return Handler
