"""Authenticated retained-session registration, sealing and independent verification."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac,json,re,sys,urllib.request,urllib.error
from ..contracts import Claim
from .model_residency import ModelLease
from .request_http import HttpRequests


def handler(authority, identities):
    identities=dict(identities)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            data=json.dumps(value).encode();self.send_response(status)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)
        def do_POST(self):
            if (self.client_address[0]!='127.0.0.1' or self.headers.get('Origin') is not None
                    or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port)):
                return self.reply(403,{'error':'RETAINED_LOOPBACK_ONLY'})
            auth=self.headers.get('Authorization','')
            identity=next((v for k,v in identities.items() if hmac.compare_digest(auth.encode(),('Bearer '+k).encode())),None)
            if identity is None or identity[1]!='animation':return self.reply(401,{'error':'RETAINED_WORKER_AUTH_REQUIRED'})
            length=self.headers.get('Content-Length','')
            if self.headers.get('Content-Type')!='application/json' or not length.isdecimal() or not 0<int(length):
                return self.reply(400,{'error':'RETAINED_BODY_INVALID'})
            self.connection.settimeout(5)
            try:
                body=json.loads(self.rfile.read(int(length)));value=body['claim'];value['input_refs']=tuple(value['input_refs']);claim=Claim(**value)
                worker=identity[0]
                if self.path=='/v1/animation-retained/register':
                    authority.register(claim,ModelLease(**body['model']),worker,body['engine'],body['prompt'])
                elif self.path=='/v1/animation-retained/seal':
                    authority.seal(claim,ModelLease(**body['model']),worker,body['prompt'])
                elif self.path=='/v1/animation-retained/verify':
                    authority.verify_report(claim,worker,body['hook'])
                else:raise ValueError('RETAINED_OPERATION_INVALID')
                return self.reply(200,{'ok':True})
            except (ValueError,KeyError,TypeError) as exc:
                code=str(exc);return self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else 'RETAINED_CONTRACT_ERROR'})
    return Handler


class HttpRetainedAnimation(HttpRequests):
    def send(self,operation,body):
        raw=json.dumps(body,separators=(',',':')).encode()
        request=urllib.request.Request(self.base+'/v1/animation-retained/'+operation,
            data=raw,
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        try:
            with self.opener.open(request,timeout=self.timeout) as response:
                data=response.read(65537)
                if len(data)>65536 or json.loads(data)!={'ok':True}:raise ValueError('RETAINED_ACK_INVALID')
        except urllib.error.HTTPError as exc:
            label={'register':'REGISTER','seal':'SEAL','verify':'VERIFY'}.get(operation,'OTHER')
            safe={'RETAINED_LOOPBACK_ONLY','RETAINED_WORKER_AUTH_REQUIRED','RETAINED_BODY_INVALID',
                'RETAINED_OPERATION_INVALID','RETAINED_CONTRACT_ERROR','RETAINED_EXECUTION_OWNER_MISMATCH',
                'RETAINED_CURRENT_FENCE_CHANGED','RETAINED_MODEL_OWNER_MISMATCH','RETAINED_IDENTITY_INVALID',
                'RETAINED_BINDING_CHANGED','RETAINED_PROMPT_LIMIT','RETAINED_TERMINAL_MISMATCH',
                'RETAINED_REPORT_MISMATCH','RETAINED_IDLE_NOT_VERIFIED'}
            try:
                value=json.loads(exc.read(4097));code=value.get('error')if isinstance(value,dict)else None
            except (ValueError,OSError):code=None
            finally:exc.close()
            code=code if isinstance(code,str) and code in safe else None
            print(json.dumps({'event':'animation_retained_http_failure','operation':label,'httpStatus':exc.code,'requestBytes':len(raw),'errorCode':code}),file=sys.stderr,flush=True)
            suffix='_'+code if code else ''
            raise RuntimeError('ANIMATION_RETAINED_'+label+'_HTTP_'+str(exc.code)+suffix)from None
    def register(self,claim,model,engine,prompt):
        self.send('register',{'claim':asdict(claim),'model':asdict(model),'engine':engine,'prompt':prompt})
    def seal(self,claim,model,prompt):
        self.send('seal',{'claim':asdict(claim),'model':asdict(model),'prompt':prompt})
    def record_execution(self,claim,worker,hook):
        if worker!=self.worker_id:raise ValueError('RETAINED_WORKER_MISMATCH')
        self.send('verify',{'claim':asdict(claim),'hook':hook})


class RetainedReporter:
    def __init__(self,terminal,retained):self.terminal,self.retained=terminal,retained
    def record_execution(self,claim,worker,hook):
        if hook and hook.get('outcome')=='retained_idle':
            return self.retained.record_execution(claim,worker,hook)
        return self.terminal.record_execution(claim,worker,hook)
    def record_model(self,lease,worker,hook):return self.terminal.record_model(lease,worker,hook)
