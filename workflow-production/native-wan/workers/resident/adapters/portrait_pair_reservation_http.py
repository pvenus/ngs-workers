"""Read an existing pair reservation using the existing PDS service identity."""
import hmac,json,re
from http.server import BaseHTTPRequestHandler
from ..contracts import Claim,PROFILES

def handler(service,tokens,*,path='/v1/npc-portrait-pair-reservations/read'):
 if path not in ('/v1/npc-portrait-pair-reservations/read','/v1/npc-pose-direction-reservations/read'):raise ValueError('RESERVATION_METADATA_ROUTE')
 tokens=tuple(tokens)
 if not tokens:raise ValueError('PUBLICATION_SERVICE_AUTH_REQUIRED')
 class Handler(BaseHTTPRequestHandler):
  def do_POST(self):
   if self.client_address[0]!='127.0.0.1'or self.headers.get('Origin')is not None or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):return self.reply(403,{'error':'LOOPBACK_ONLY'})
   if not any(hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+t)for t in tokens):return self.reply(403,{'error':'PUBLICATION_SERVICE_AUTH_REQUIRED'})
   try:
    if self.path!=path:return self.reply(404,{'error':'NOT_FOUND'})
    n=self.headers.get('Content-Length','')
    if not n.isdecimal()or not 0<int(n)<=1024*1024 or self.headers.get('Content-Type')!='application/json'or self.headers.get('Transfer-Encoding'):raise ValueError('PAIR_RESERVATION_BODY')
    self.connection.settimeout(5);raw=self.rfile.read(int(n))
    if len(raw)!=int(n):raise ValueError('PAIR_RESERVATION_BODY')
    body=json.loads(raw)
    if set(body)!={'claim','packetHash'}or not isinstance(body['packetHash'],str)or not re.fullmatch('[0-9a-f]{64}',body['packetHash']):raise ValueError('PAIR_RESERVATION_COMMAND')
    v=body['claim'];v['input_refs']=tuple(v['input_refs']);claim=Claim(**v);claim.validate(PROFILES['image'])
    return self.reply(200,service.read_reservation(claim,body['packetHash']))
   except (ValueError,KeyError,TypeError)as exc:
    code=str(exc);return self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'PAIR_RESERVATION_REJECTED'})
  def reply(self,status,value):
   raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def log_message(self,*args):pass
 return Handler
