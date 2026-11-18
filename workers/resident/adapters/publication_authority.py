"""Service-only live publication authorization, not a worker-supplied proof."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac,json
from ..contracts import Claim
from .pds_category_files import CategoryFileClient,encode

def handler(bridge,service_tokens):
    tokens=tuple(service_tokens)
    if not tokens or any(not isinstance(t,str) or len(t)<32 for t in tokens):raise ValueError('SERVICE_IDENTITIES_REQUIRED')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def reply(self,status,value):
            data=encode(value);self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            if self.client_address[0]!='127.0.0.1' or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port) or self.headers.get('Origin') is not None:return self.reply(403,{'error':'LOOPBACK_ONLY'})
            if self.path not in ('/v1/publications/authorize','/v1/publications/prepare','/v1/publications/finish'):return self.reply(404,{'error':'NOT_FOUND'})
            auth=self.headers.get('Authorization','')
            if not auth.startswith('Bearer ') or not any(hmac.compare_digest(auth[7:],t) for t in tokens):return self.reply(401,{'error':'SERVICE_AUTH_REQUIRED'})
            length=self.headers.get('Content-Length','')
            if self.headers.get('Content-Type')!='application/json' or not length.isdecimal() or not 0<int(length)<=1024*1024:return self.reply(400,{'error':'BODY_INVALID'})
            self.connection.settimeout(5)
            try:
                body=json.loads(self.rfile.read(int(length)))
                operation=self.path.rsplit('/',1)[1]
                fields={'authorize':{'claim'},'prepare':{'claim','publicationHash'},'finish':{'claim','publicationHash','receipt'}}
                if set(body)!=fields[operation]:raise ValueError('BODY_INVALID')
                raw=body['claim'];raw['input_refs']=tuple(raw['input_refs']);c=Claim(**raw)
                if operation=='prepare':return self.reply(200,bridge.prepare_publication(c,body['publicationHash']))
                if operation=='finish':return self.reply(200,bridge.finish_publication(c,body['publicationHash'],body['receipt']))
                return self.reply(200,bridge.authorize_publication(c))
            except (ValueError,TypeError,KeyError):return self.reply(409,{'error':'PUBLICATION_NOT_AUTHORIZED'})
    return Handler

class PublicationAuthorityClient:
    def __init__(self,base,service_token,*,timeout=5):
        import urllib.request
        from .pds_category_files import NoRedirect
        from urllib.parse import urlsplit
        url=urlsplit(base)
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.path or url.query or url.fragment or url.username or not 0<timeout<=5:raise ValueError('PUBLICATION_AUTHORITY_CONFIG')
        self.base,self.token,self.timeout=base,service_token,timeout
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def _call(self,operation,claim,**extra):
        import urllib.request
        req=urllib.request.Request(self.base+'/v1/publications/'+operation,data=encode({'claim':asdict(claim),**extra}),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with self.opener.open(req,timeout=self.timeout) as r:
            raw=r.read(65537)
        if len(raw)>65536:raise ValueError('PUBLICATION_AUTHORITY_RESPONSE')
        value=json.loads(raw)
        if operation!='authorize':return value
        expected={'requestId':claim.request_id,'attemptId':claim.attempt_id,'fencingToken':claim.fencing_token,'capability':claim.capability,'expectedRevision':claim.expected_revision}
        if value.get('authorized') is not True or any(value.get(k)!=v for k,v in expected.items()):raise ValueError('PUBLICATION_AUTHORITY_BINDING')
        return value

    def __call__(self,claim):return self._call('authorize',claim)
    def prepare(self,claim,publication_hash):
        value=self._call('prepare',claim,publicationHash=publication_hash)
        if value.get('reserved') is not True or value.get('publicationHash')!=publication_hash or value.get('requestId')!=claim.request_id or value.get('attemptId')!=claim.attempt_id or value.get('fencingToken')!=claim.fencing_token:raise ValueError('PUBLICATION_RESERVATION_BINDING')
        return value
    def finish(self,claim,publication_hash,receipt):
        value=self._call('finish',claim,publicationHash=publication_hash,receipt=receipt)
        if value.get('committed') is not True or value.get('publicationHash')!=publication_hash or value.get('requestId')!=claim.request_id:raise ValueError('PUBLICATION_COMMIT_BINDING')
        return value
