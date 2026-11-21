"""Authenticated terminal reports from reviewed local workers, never user DTOs."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac
import json
import re
from .request_http import HttpRequests
from ..contracts import Claim,PROFILES
from .model_residency import ModelLease


def handler(authority,identities):
    identities=dict(identities)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            data=json.dumps(value,separators=(',',':')).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            if (self.client_address[0]!='127.0.0.1' or self.headers.get('Origin') is not None
                    or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port)):
                return self.reply(403,{'error':'TERMINAL_LOOPBACK_ONLY'})
            token=self.headers.get('Authorization','')
            identity=next((v for k,v in identities.items()if hmac.compare_digest(token.encode(),('Bearer '+k).encode())),None)
            if identity is None:return self.reply(401,{'error':'TERMINAL_WORKER_AUTH_REQUIRED'})
            worker,role=identity
            length=self.headers.get('Content-Length','')
            if (self.headers.get('Content-Type')!='application/json' or not length.isdecimal()or int(length)<=0):
                return self.reply(400,{'error':'TERMINAL_BODY_INVALID'})
            self.connection.settimeout(5)
            try:
                from ..terminal_evidence import TerminalEvidence,ProcessIdentity
                body=json.loads(self.rfile.read(int(length)))
                if not isinstance(body,dict) or set(body)!={'value','evidence'}:raise ValueError('TERMINAL_BODY_INVALID')
                raw=body['evidence'];raw['processes']=tuple(ProcessIdentity(**p)for p in raw['processes'])
                raw['return_codes']=tuple(raw['return_codes']);evidence=TerminalEvidence(**raw)
                if self.path=='/v1/terminal-evidence/execution':
                    value=body['value'];value['input_refs']=tuple(value['input_refs']);claim=Claim(**value)
                    claim.validate(PROFILES[role]);authority.record_execution(claim,worker,evidence)
                elif self.path=='/v1/terminal-evidence/model':
                    lease=ModelLease(**body['value'])
                    if lease.worker_id!=worker or lease.role!=role:raise ValueError('TERMINAL_WORKER_MISMATCH')
                    authority.record_model(lease,worker,evidence)
                else:raise ValueError('TERMINAL_OPERATION_INVALID')
                return self.reply(200,{'ok':True})
            except (ValueError,KeyError,TypeError) as exc:
                code=str(exc)
                return self.reply(409,{'error':code if re.fullmatch('[A-Z0-9_]{1,160}',code)else'TERMINAL_CONTRACT_ERROR'})
    return Handler

class HttpTerminalEvidence(HttpRequests):
    def _record(self,kind,value,worker_id,evidence):
        if worker_id!=self.worker_id:raise ValueError('TERMINAL_WORKER_MISMATCH')
        # Parent transport uses /v1/workers; isolated client base targets the
        # evidence prefix through its own bounded request method below.
        body={'value':asdict(value),'evidence':asdict(evidence)}
        try:return self._evidence_call(kind,body)
        except (OSError,TimeoutError):return self._evidence_call(kind,body)
    def _evidence_call(self,kind,body):
        import urllib.request
        request=urllib.request.Request(self.base+'/v1/terminal-evidence/'+kind,
            data=json.dumps(body,separators=(',',':')).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with self.opener.open(request,timeout=self.timeout)as response:
            raw=response.read(65537)
            if len(raw)>65536:raise ValueError('TERMINAL_RESPONSE_TOO_LARGE')
            result=json.loads(raw)
            if result!={'ok':True}:raise ValueError('TERMINAL_ACK_INVALID')
            return result
    def record_execution(self,claim,worker_id,evidence):return self._record('execution',claim,worker_id,evidence)
    def record_model(self,lease,worker_id,evidence):return self._record('model',lease,worker_id,evidence)
