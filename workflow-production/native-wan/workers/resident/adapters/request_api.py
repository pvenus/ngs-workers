"""Request-SERVICE HTTP facade. No automatic startup or production DB binding.

New internal API, not a claim about existing NGS endpoints. Host only in Request
service; workers import request_http, never this module/common_authority.
"""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler
import hmac
import json
import re
from ..contracts import Claim,Receipt,PROFILES


def handler(bridge,identities):
    # identities: operator-provisioned token -> (workerId, canonical role).
    # Production provisioning is deliberately absent from the worker launcher.
    identities=dict(identities)
    if any(not token or role not in PROFILES for token,(_,role) in identities.items()):
        raise ValueError('WORKER_IDENTITIES_INVALID')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,value):
            body=json.dumps(value,separators=(',',':')).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            if (self.client_address[0]!='127.0.0.1' or self.headers.get('Origin') is not None
                    or self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port)):
                return self.reply(403,{'error':'LOOPBACK_ONLY'})
            authorization=self.headers.get('Authorization','')
            if not authorization.startswith('Bearer '):
                return self.reply(401,{'error':'WORKER_AUTH_REQUIRED'})
            token=authorization[7:]
            identity=next((v for k,v in identities.items() if hmac.compare_digest(token,k)),None)
            if identity is None:return self.reply(401,{'error':'WORKER_AUTH_REQUIRED'})
            worker_id,role=identity
            length=self.headers.get('Content-Length','')
            if (self.headers.get('Content-Type')!='application/json' or not length.isdecimal()
                    or not 0<int(length)<=1024*1024):
                return self.reply(400,{'error':'BODY_INVALID'})
            self.connection.settimeout(5)
            try:
                body=json.loads(self.rfile.read(int(length)))
                if not isinstance(body,dict):raise ValueError('BODY_INVALID')
                operation=self.path.removeprefix('/v1/workers/')
                if self.path!='/v1/workers/'+operation:raise ValueError('OPERATION_INVALID')
                if operation=='claim':
                    if set(body)!={'workerId','role','claimKey'} or body['workerId']!=worker_id or body['role']!=role:
                        raise ValueError('WORKER_SCOPE_MISMATCH')
                    if not isinstance(body['claimKey'],str) or not re.fullmatch('[a-f0-9]{32}',body['claimKey']):raise ValueError('CLAIM_KEY_INVALID')
                    c=bridge.claim_idempotent(PROFILES[role],worker_id,body['claimKey'])
                    return self.reply(200,{'claim':asdict(c) if c else None})
                raw=body['claim'];raw['input_refs']=tuple(raw['input_refs']);c=Claim(**raw)
                c.validate(PROFILES[role])
                # Grant owner comes from the service ledger, not client fields.
                with bridge.transaction():
                    lease=(bridge._issued_lease(c,allow_released=True) if operation in ('release','keepalive-lost') else bridge._lease(c))
                    if lease['workerId']!=worker_id:raise ValueError('WORKER_SCOPE_MISMATCH')
                if operation=='keepalive-lost' and set(body)=={'claim','reason'}:
                    return self.reply(200,bridge.keepalive_lost(c,body['reason']))
                if operation=='ownership-guard' and set(body)=={'claim'}:
                    return self.reply(200,{'guard':bridge.ownership_guard(c)})
                if operation=='native-workspace-refresh' and set(body)=={'claim'}:
                    return self.reply(200,bridge.refresh_native_workspace(c))
                if operation=='renew' and set(body)=={'claim'}:
                    return self.reply(200,{'renewed':bridge.renew(c)})
                if operation=='publication-started' and set(body)=={'claim'}:
                    bridge.mark_publication_started(c)
                elif operation=='checkpoint' and set(body)=={'claim','receipt'}:
                    r=body['receipt'];r['asset_refs']=tuple(r['asset_refs'])
                    bridge.checkpoint(c,Receipt(**r))
                elif operation=='complete' and set(body)=={'claim','receipt','cleanupDone'}:
                    if type(body['cleanupDone']) is not bool:raise ValueError('CLEANUP_FLAG_INVALID')
                    r=body['receipt'];r['asset_refs']=tuple(r['asset_refs'])
                    bridge.complete(c,Receipt(**r),body['cleanupDone'])
                elif operation=='fail' and set(body)=={'claim','code','cleanupDone'}:
                    if type(body['cleanupDone']) is not bool:raise ValueError('CLEANUP_FLAG_INVALID')
                    if not re.fullmatch(r'[A-Za-z0-9_]{1,160}',body['code']):raise ValueError('ERROR_CODE_INVALID')
                    bridge.fail(c,body['code'],body['cleanupDone'])
                elif operation=='uncertain' and set(body)=={'claim','code'}:
                    if not re.fullmatch(r'[A-Za-z0-9_]{1,160}',body['code']):raise ValueError('ERROR_CODE_INVALID')
                    bridge.uncertain(c,body['code'])
                elif operation=='execution-unknown' and set(body)=={'claim','code'}:
                    bridge.execution_unknown(c,'EXECUTION_STOP_UNCONFIRMED')
                elif operation=='release' and set(body)=={'claim'}:bridge.release_resource(c)
                else:raise ValueError('OPERATION_INVALID')
                return self.reply(200,{'ok':True})
            except (ValueError,KeyError,TypeError) as exc:
                code=str(exc)
                if not re.fullmatch(r'[A-Z0-9_]{1,160}',code):code='REQUEST_CONTRACT_ERROR'
                return self.reply(409,{'error':code})
    return Handler
